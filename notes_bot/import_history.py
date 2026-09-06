# ---------------- Разовый импорт истории чата ----------------
# Bot API не отдаёт сообщения, отправленные до того, как бот попал в чат,
# поэтому историю заливаем из JSON-экспорта Telegram Desktop:
#   Настройки → Экспорт данных → выбрать чат → формат JSON.
#
# Работает в два прохода:
#   1) все сообщения складываются в базу как есть (быстро, без модели);
#   2) неразобранные пачками уходят в DeepSeek.
# Второй проход можно прерывать и перезапускать сколько угодно: чекпоинт —
# это само поле type, и уже разобранное второй раз в модель не пойдёт.
#
# Запуск:
#   python import_history.py result.json
#   python import_history.py result.json --ocr --batch 20 --concurrency 2
#   python import_history.py --classify-only          # дозапустить разбор

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time

import aiohttp

import classify
import db
import ocr
from config import NOTES_CHAT_ID

MEDIA_TYPES = {
    "voice_message": ("voice", 1),
    "video_message": ("video_note", 1),
    "audio_file": ("audio", 1),
    "video_file": ("video", 0),
    "animation": ("animation", 0),
    "sticker": ("sticker", 0),
}


# ---------------- Разбор экспорта ----------------

def flatten_text(value):
    """В экспорте text — это либо строка, либо список из строк и объектов
    вида {"type": "link", "text": "..."}. Приводим к плоской строке."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def normalize_chat_id(export):
    """id чата в экспорте — «сырой», без префикса. Bot API видит супергруппы
    как -100<id>, обычные группы как -<id>. Без этого преобразования импорт
    и живой бот сложат одни и те же сообщения в разные чаты и продублируют
    всю историю."""
    raw = export.get("id")
    if raw is None:
        return None

    raw = int(raw)
    if raw < 0:
        return raw

    chat_type = (export.get("type") or "").lower()
    if "supergroup" in chat_type or "channel" in chat_type:
        return int(f"-100{raw}")
    if "group" in chat_type:
        return -raw
    return raw


def message_date(message):
    unixtime = message.get("date_unixtime")
    if unixtime:
        try:
            return float(unixtime)
        except (TypeError, ValueError):
            pass
    raw = message.get("date")
    if raw:
        try:
            return dt.datetime.fromisoformat(str(raw)).timestamp()
        except Exception:
            pass
    return time.time()


def media_of(message):
    """(тип вложения, нужна ли расшифровка, относительный путь к файлу)"""
    if message.get("photo"):
        return "photo", 0, message.get("photo")

    media_type = message.get("media_type")
    if media_type in MEDIA_TYPES:
        kind, transcribe = MEDIA_TYPES[media_type]
        return kind, transcribe, message.get("file")

    if message.get("file"):
        return "document", 0, message.get("file")
    return None, 0, None


def iter_messages(export, since=None):
    for message in export.get("messages") or []:
        # Служебные записи (вступил в группу, закрепил сообщение) — не заметки.
        if message.get("type") != "message":
            continue

        date = message_date(message)
        if since is not None and date < since:
            continue

        text = flatten_text(message.get("text")).strip()
        media, transcribe, path = media_of(message)

        if not text and not media:
            continue

        yield {
            "message_id": int(message.get("id")),
            "date": date,
            "text": text,
            "media": media,
            "needs_transcription": transcribe,
            "path": path,
        }


# ---------------- Проход 1: заливка в базу ----------------

def load_into_db(path, chat_id, since=None, use_ocr=False, limit=None, dry_run=False):
    with open(path, encoding="utf-8") as f:
        export = json.load(f)

    resolved_chat_id = chat_id or normalize_chat_id(export)
    if not resolved_chat_id:
        print("❌ Не удалось определить chat_id. Передай его явно: --chat-id -1001234567890")
        return None, 0, 0

    export_dir = os.path.dirname(os.path.abspath(path))
    print(f"Чат: {export.get('name')} (id {resolved_chat_id})")

    added = skipped = 0
    for index, item in enumerate(iter_messages(export, since=since)):
        if limit and added >= limit:
            break

        ocr_text = None
        if use_ocr and item["media"] == "photo" and item["path"]:
            full = os.path.join(export_dir, item["path"])
            if os.path.isfile(full):
                ocr_text = ocr.file_to_text(full) or None

        if dry_run:
            added += 1
            if added <= 10:
                preview = " ".join(item["text"].split())[:90]
                when = dt.datetime.fromtimestamp(item["date"]).strftime("%d.%m.%Y")
                print(f"  [{when}] {item['media'] or 'текст':9} {preview}")
            continue

        _, is_new = db.add_note(
            chat_id=resolved_chat_id,
            message_id=item["message_id"],
            date=item["date"],
            text=item["text"],
            ocr_text=ocr_text,
            media=item["media"],
            needs_transcription=item["needs_transcription"],
            source="import",
        )
        if is_new:
            added += 1
        else:
            skipped += 1

        if (index + 1) % 500 == 0:
            print(f"  …прочитано {index + 1}, новых {added}")

    return resolved_chat_id, added, skipped


# ---------------- Проход 2: классификация ----------------

async def classify_pending(chat_id=None, batch_size=20, concurrency=2, limit=None):
    total = db.count_unclassified(chat_id)
    if not total:
        print("Всё уже разобрано.")
        return

    target = min(total, limit) if limit else total
    print(f"К разбору: {target} заметок, пачками по {batch_size}, потоков {concurrency}")

    done = 0
    failed = 0
    started = time.time()

    async with aiohttp.ClientSession() as session:
        semaphore = asyncio.Semaphore(concurrency)

        while done + failed < target:
            rows = db.unclassified(limit=batch_size * concurrency, chat_id=chat_id)
            if not rows:
                break

            batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

            async def run(batch):
                nonlocal done, failed
                items = []
                for position, row in enumerate(batch):
                    items.append({
                        "i": position,
                        "date": dt.datetime.fromtimestamp(row["date"]).strftime("%Y-%m-%d"),
                        "text": db.note_text(row),
                    })

                async with semaphore:
                    results = await classify.classify_batch(items, session=session)

                for position, row in enumerate(batch):
                    result = results.get(position)
                    if not result:
                        # Помечаем как разобранное с пустым проектом, иначе цикл
                        # будет вечно возвращать одни и те же битые заметки.
                        db.update_classification(
                            row["id"], type="thought", project="none",
                            tags=[], summary=None, due=None,
                        )
                        failed += 1
                        continue

                    db.update_classification(
                        row["id"],
                        type=result["type"], project=result["project"],
                        tags=result["tags"], summary=result["summary"], due=result["due"],
                    )
                    done += 1

            await asyncio.gather(*(run(batch) for batch in batches))

            elapsed = time.time() - started
            speed = (done + failed) / elapsed if elapsed else 0
            left = (target - done - failed) / speed if speed else 0
            print(f"  разобрано {done + failed}/{target} "
                  f"(не далось модели: {failed}), осталось ~{int(left // 60)} мин")

    print(f"Готово. Разобрано {done}, не далось {failed}.")


# ---------------- CLI ----------------

def main():
    parser = argparse.ArgumentParser(description="Импорт истории чата заметок из JSON-экспорта Telegram")
    parser.add_argument("path", nargs="?", help="путь к result.json")
    parser.add_argument("--chat-id", type=int, default=NOTES_CHAT_ID or None,
                        help="chat_id, если не определяется из экспорта")
    parser.add_argument("--since", help="импортировать только с этой даты, ГГГГ-ММ-ДД")
    parser.add_argument("--batch", type=int, default=20, help="сообщений в одном запросе к модели")
    parser.add_argument("--concurrency", type=int, default=2, help="параллельных запросов")
    parser.add_argument("--limit", type=int, help="ограничить число сообщений (для пробы)")
    parser.add_argument("--ocr", action="store_true", help="распознавать текст на выгруженных фото")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет импортировано")
    parser.add_argument("--import-only", action="store_true", help="залить в базу, но не классифицировать")
    parser.add_argument("--classify-only", action="store_true", help="только дозапустить классификацию")
    args = parser.parse_args()

    db.init()

    chat_id = args.chat_id

    if not args.classify_only:
        if not args.path:
            parser.error("укажи путь к result.json (или используй --classify-only)")

        since = None
        if args.since:
            since = dt.datetime.fromisoformat(args.since).timestamp()

        chat_id, added, skipped = load_into_db(
            args.path, args.chat_id, since=since, use_ocr=args.ocr,
            limit=args.limit, dry_run=args.dry_run,
        )
        if chat_id is None:
            sys.exit(1)

        if args.dry_run:
            print(f"\nПробный прогон: к импорту {added} сообщений. Ничего не записано.")
            return

        print(f"\nЗалито в базу: {added} новых, пропущено как уже известные: {skipped}")

        if args.import_only:
            print("Классификация пропущена (--import-only). "
                  "Запусти позже: python import_history.py --classify-only")
            return

    asyncio.run(classify_pending(
        chat_id=chat_id, batch_size=args.batch,
        concurrency=args.concurrency, limit=args.limit,
    ))


if __name__ == "__main__":
    main()
