# ---------------- Личный ассистент по чату заметок ----------------
# Что делает:
#   • ловит каждое сообщение в чате заметок, классифицирует его через DeepSeek
#     и кладёт в SQLite; отвечает не текстом, а реакцией — чат остаётся чистым;
#   • принимает исправления реплаем («#задача #адыгид») и учится на них;
#   • отвечает на запросы, начинающиеся с «бро»;
#   • сам присылает утренний и недельный дайджест;
#   • предлагает объединить дубликаты и вывести новые правила — всегда с кнопкой,
#     ничего в базе не меняется без подтверждения.

import asyncio
import datetime as dt
import json
import logging
import os
import time

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update,
)
from telegram.constants import ChatAction
from telegram.error import Conflict, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, MessageReactionHandler, filters,
)

import classify
import commands
import db
import digest
import import_history
import ocr
from config import (
    AUTODELETE_ENABLED, AUTODELETE_MINUTES, BOT_TOKEN, DAILY_ENABLED,
    DAILY_HOUR, KEEP_REACTION, NOTES_CHAT_ID, OWNER_ID, plain_emoji,
    REACTION_DONE, REACTION_PROCESSING, REACTION_UNKNOWN, TZ,
    WEEKLY_ENABLED, WEEKLY_HOUR, WEEKLY_WEEKDAY,
    project_title, type_reaction, type_title,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("notes")


# ---------------- Доступ ----------------

def _target_chat():
    return NOTES_CHAT_ID or OWNER_ID


async def _setup_hint(update):
    """Режим первичной настройки: пока не заданы OWNER_ID/NOTES_CHAT_ID, бот
    ничего не пишет в базу, а подсказывает, какие значения прописать."""
    chat = update.effective_chat
    user = update.effective_user
    text = (
        "Я ещё не настроен. Пропиши в переменные окружения:\n"
        f"NOTES_OWNER_ID={user.id if user else '?'}\n"
        f"NOTES_CHAT_ID={chat.id if chat else '?'}\n"
        "и перезапусти меня."
    )
    log.warning("Не настроен: owner=%s chat=%s", user.id if user else None, chat.id if chat else None)
    try:
        await _reply(update.effective_message, text)
    except TelegramError as e:
        log.warning("Не смог ответить подсказкой: %s", e)


def _allowed(update):
    user = update.effective_user
    chat = update.effective_chat

    if not OWNER_ID:
        return False
    if not user or user.id != OWNER_ID:
        return False
    if NOTES_CHAT_ID and (not chat or chat.id != NOTES_CHAT_ID):
        return False
    return True


# ---------------- Отправка с самоочисткой ----------------
# Всё, что бот пишет сам, попадает в bot_messages и удаляется через
# AUTODELETE_MINUTES. Пометил сердечком — сообщение остаётся навсегда.
# Поэтому любая отправка идёт только через эти две обёртки: если завести
# отдельный reply_text мимо них, сообщение останется в чате навсегда.

async def _track(message):
    if message is not None and AUTODELETE_ENABLED:
        try:
            db.track_bot_message(message.chat_id, message.message_id)
        except Exception as e:
            log.warning("Не записал своё сообщение для самоочистки: %s", e)
    return message


async def _reply(message, text, **kwargs):
    return await _track(await message.reply_text(text, **kwargs))


async def _send(bot, chat_id, text, **kwargs):
    return await _track(await bot.send_message(chat_id=chat_id, text=text, **kwargs))


# ---------------- Реакции ----------------

_reaction_failures = 0
_REACTION_GIVE_UP = 5


async def react(bot, chat_id, message_id, emoji):
    """Ставит одну реакцию (или снимает, если emoji=None).

    Telegram принимает только эмодзи из своего фиксированного набора, а в чате
    набор может быть дополнительно урезан настройками. Если реакции стабильно
    не проходят — перестаём пытаться, чтобы не сыпать ошибками на каждое
    сообщение: заметки важнее, чем галочка."""
    global _reaction_failures

    if _reaction_failures >= _REACTION_GIVE_UP:
        return

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji)] if emoji else [],
        )
        _reaction_failures = 0
    except TelegramError as e:
        _reaction_failures += 1
        log.warning("Реакция %s не поставилась: %s", emoji, e)
        if _reaction_failures >= _REACTION_GIVE_UP:
            log.error("Реакции отключены после %d ошибок подряд", _REACTION_GIVE_UP)


# ---------------- Сердечко отменяет удаление ----------------

async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Владелец поставил или снял реакцию. Интересует только сердечко на
    сообщении самого бота: оно значит «оставить навсегда». Реакции на обычные
    заметки сюда не долетают — для них в bot_messages просто нет строки."""
    event = update.message_reaction
    if event is None:
        return
    if OWNER_ID and (event.user is None or event.user.id != OWNER_ID):
        return
    if NOTES_CHAT_ID and event.chat.id != NOTES_CHAT_ID:
        return

    emojis = {plain_emoji(getattr(r, "emoji", None)) for r in event.new_reaction}
    keep = plain_emoji(KEEP_REACTION) in emojis

    if db.set_bot_message_keep(event.chat.id, event.message_id, keep):
        log.info("Своё сообщение %s: %s", event.message_id,
                 "оставляю навсегда" if keep else "снова в очереди на удаление")


async def _sweep(application):
    """Убирает свои сообщения, прожившие дольше положенного и не отмеченные
    сердечком. Ошибку удаления гасим: сообщение могли стереть вручную, а
    старше 48 часов Telegram удалять и не даёт — в обоих случаях просто
    забываем о нём, чтобы не пытаться вечно."""
    if not AUTODELETE_ENABLED:
        return

    rows = db.expired_bot_messages(time.time() - AUTODELETE_MINUTES * 60)
    if not rows:
        return

    removed = 0
    for row in rows:
        try:
            await application.bot.delete_message(row["chat_id"], row["message_id"])
            removed += 1
        except TelegramError as e:
            log.debug("Не удалил своё сообщение %s: %s", row["message_id"], e)
        db.forget_bot_message(row["chat_id"], row["message_id"])
        await asyncio.sleep(0.2)  # чтобы не упереться во флуд-лимит

    log.info("Самоочистка: удалено %d из %d", removed, len(rows))


# ---------------- Разбор сообщения ----------------

def _media_info(message):
    """(тип вложения, file_id, нужна ли расшифровка)"""
    if message.photo:
        return "photo", message.photo[-1].file_id, 0
    if message.voice:
        return "voice", message.voice.file_id, 1
    if message.video_note:
        return "video_note", message.video_note.file_id, 1
    if message.audio:
        return "audio", message.audio.file_id, 1
    if message.video:
        return "video", message.video.file_id, 0
    if message.animation:
        return "animation", message.animation.file_id, 0
    if message.document:
        return "document", message.document.file_id, 0
    if message.sticker:
        return "sticker", message.sticker.file_id, 0
    return None, None, 0


def _forward_label(message):
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None

    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    if chat is not None:
        name = chat.title or chat.username or "канал"
        return f"переслано из «{name}»"

    user = getattr(origin, "sender_user", None)
    if user is not None:
        return f"переслано от {user.full_name}"

    name = getattr(origin, "sender_user_name", None)
    if name:
        return f"переслано от {name}"
    return "переслано"


async def _photo_text(message, bot):
    if not message.photo or not ocr.available():
        return ""
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        data = bytes(await file.download_as_bytearray())
    except Exception as e:
        log.warning("Не удалось скачать фото: %s: %s", type(e).__name__, e)
        return ""
    return await asyncio.to_thread(ocr.image_to_text, data)


async def _classify_note(note_id, bot, chat_id, message_id, extra=None):
    """Классифицирует уже сохранённую заметку и обновляет реакцию."""
    note = db.get_note(note_id)
    if note is None:
        return

    text = db.note_text(note)
    if not text.strip():
        # Голосовое или видео без подписи — разобрать нечего, помечаем и ждём
        # расшифровки. Заметка при этом уже в базе и не потеряется.
        await react(bot, chat_id, message_id, REACTION_UNKNOWN)
        return

    when = dt.datetime.fromtimestamp(note["date"], TZ).strftime("%Y-%m-%d")
    hints = [h for h in (extra, note["media"] and f"вложение — {note['media']}") if h]
    result = await classify.classify(text, date=when, extra="; ".join(hints) or None)

    if not result:
        await react(bot, chat_id, message_id, REACTION_UNKNOWN)
        return

    db.update_classification(
        note_id,
        type=result["type"], project=result["project"],
        tags=result["tags"], summary=result["summary"], due=result["due"],
    )
    await react(bot, chat_id, message_id, type_reaction(result["type"]))


# ---------------- Импорт истории присланным файлом ----------------
# Самый неудобный момент всей затеи: база живёт на томе Railway, а экспорт
# лежит на домашнем компьютере, и положить файл в контейнер нечем. Поэтому
# экспорт можно просто кинуть в чат файлом — бот скачает и разберёт его сам.
# Ограничение Bot API на скачивание — 20 МБ; экспорт без медиа в него
# укладывается даже на десятках тысяч сообщений.

MAX_EXPORT_BYTES = 20 * 1024 * 1024


def _export_path(message_id):
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), f"import_{message_id}.json")


async def _offer_import(update, context):
    """Прислали result.json — считаем, что там, и спрашиваем подтверждение."""
    message = update.effective_message
    document = message.document

    if document.file_size and document.file_size > MAX_EXPORT_BYTES:
        await _reply(message, 
            f"Файл {document.file_size // 1024 // 1024} МБ — Telegram не даёт ботам скачивать "
            "больше 20 МБ. Выгрузи экспорт без медиа или запусти импорт с компьютера:\n"
            "python import_history.py result.json"
        )
        return

    await react(context.bot, message.chat_id, message.message_id, REACTION_PROCESSING)

    path = _export_path(message.message_id)
    try:
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(path)
        with open(path, encoding="utf-8") as f:
            export = json.load(f)
    except Exception as e:
        log.warning("Экспорт не прочитался: %s: %s", type(e).__name__, e)
        await _reply(message, f"Не смог прочитать файл: {type(e).__name__}. Нужен JSON-экспорт из Telegram Desktop.")
        await react(context.bot, message.chat_id, message.message_id, REACTION_UNKNOWN)
        return

    if not isinstance(export, dict) or "messages" not in export:
        os.remove(path)
        await _reply(message, 
            "Это не похоже на экспорт чата. Нужен result.json из Telegram Desktop: "
            "Настройки → Экспорт данных, формат JSON."
        )
        await react(context.bot, message.chat_id, message.message_id, REACTION_UNKNOWN)
        return

    chat_id = import_history.normalize_chat_id(export) or NOTES_CHAT_ID
    total = sum(1 for _ in import_history.iter_messages(export))

    if chat_id != NOTES_CHAT_ID:
        log.warning("Экспорт из другого чата: %s (текущий %s)", chat_id, NOTES_CHAT_ID)

    await _reply(message, 
        f"Экспорт «{export.get('name')}»: {total} сообщений к разбору.\n"
        f"Уже в базе: {db.stats()['total']}.\n\n"
        "Заливаю в базу и прогоняю через модель? Уже разобранное второй раз не пойдёт.",
        reply_markup=_keyboard([[("Импортировать", f"imp:{message.message_id}"),
                                 ("Отмена", f"noimp:{message.message_id}")]]),
    )
    await react(context.bot, message.chat_id, message.message_id, None)


async def _run_import(application, path, chat_id, chat_to_report):
    """Долгий разбор: крутится фоном, чтобы бот продолжал ловить заметки."""
    bot = application.bot
    try:
        _, added, skipped = await asyncio.to_thread(
            import_history.load_into_db, path, chat_id
        )
        await _send(bot, 
            chat_to_report,
            f"Залил {added} новых, {skipped} уже были. Разбираю — это займёт время, "
            "заметки при этом продолжаю принимать.",
        )

        pending = db.count_unclassified(chat_id)
        await import_history.classify_pending(chat_id=chat_id, batch_size=20, concurrency=2)
        left = db.count_unclassified(chat_id)

        await _send(bot, 
            chat_to_report,
            f"Импорт закончен: разобрано {pending - left} заметок.\n"
            f"Всего в базе: {db.stats()['total']}.\n\n"
            "Теперь работает «бро, ...» по всей истории. Попробуй: «бро, что у меня по адыгиду».",
        )
    except Exception as e:
        log.exception("Импорт упал")
        await _send(bot, chat_to_report, f"Импорт упал: {type(e).__name__}: {e}")
    finally:
        application.bot_data["import_running"] = False
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------- Хэндлеры ----------------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return

    if not OWNER_ID or not NOTES_CHAT_ID:
        if not OWNER_ID or (update.effective_user and update.effective_user.id == OWNER_ID):
            await _setup_hint(update)
        return

    if not _allowed(update):
        return

    chat_id = message.chat_id
    text = (message.text or message.caption or "").strip()

    reply = message.reply_to_message
    replied_to_bot = bool(
        reply is not None and reply.from_user is not None
        and reply.from_user.id == context.bot.id
    )

    # 1. Исправление классификации реплаем — только на собственные заметки.
    if reply is not None and not replied_to_bot:
        parsed = classify.parse_correction(text)
        if parsed is not None:
            await _apply_correction(update, context, parsed)
            return

    # 2. Команда «бро, ...» — или ответ реплаем на сообщение бота, что то же
    # самое: это продолжение разговора, а не новая заметка. Предыдущий ответ
    # передаём как контекст, иначе «а где сама ссылка» не значит ничего.
    if commands.is_command(text) or (replied_to_bot and text):
        await _handle_bro(
            update, context, text,
            context_text=(reply.text or reply.caption) if replied_to_bot else None,
        )
        return

    # 3. Присланный экспорт истории — это не заметка, а импорт.
    document = message.document
    if document is not None and (document.file_name or "").lower().endswith(".json"):
        await _offer_import(update, context)
        return

    # 4. Обычная заметка.
    media, file_id, needs_transcription = _media_info(message)
    note_id, is_new = db.add_note(
        chat_id=chat_id,
        message_id=message.message_id,
        date=message.date.timestamp(),
        text=text,
        media=media,
        file_id=file_id,
        needs_transcription=needs_transcription,
        source="live",
    )

    if note_id is None:
        log.error("Не удалось сохранить сообщение %s", message.message_id)
        return
    if not is_new:
        return  # уже разобрано (например, приехало из импорта истории)

    await react(context.bot, chat_id, message.message_id, REACTION_PROCESSING)

    ocr_text = await _photo_text(message, context.bot)
    if ocr_text:
        db.update_text(note_id, text, ocr_text=ocr_text)

    # Источник пересылки — важная подсказка классификатору: репост из
    # туристического канала почти наверняка материал для Адыгида.
    await _classify_note(
        note_id, context.bot, chat_id, message.message_id,
        extra=_forward_label(message),
    )


async def on_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заметку отредактировали — переразбираем, иначе классификация будет
    относиться к тексту, которого больше нет."""
    message = update.edited_message
    if message is None or not _allowed(update):
        return

    note = db.get_note_by_message(message.chat_id, message.message_id)
    if note is None:
        return

    text = (message.text or message.caption or "").strip()
    if not text or text == (note["text"] or ""):
        return

    db.update_text(note["id"], text)
    await react(context.bot, message.chat_id, message.message_id, REACTION_PROCESSING)
    await _classify_note(note["id"], context.bot, message.chat_id, message.message_id)


async def _apply_correction(update, context, parsed):
    message = update.effective_message
    target = message.reply_to_message
    new_type, new_project, new_status = parsed

    note = db.get_note_by_message(message.chat_id, target.message_id)
    if note is None:
        await _reply(message, "Этой заметки нет в базе — не могу её поправить.")
        return

    changed = False
    if new_type or new_project:
        changed = db.apply_correction(note["id"], new_type=new_type, new_project=new_project)
    if new_status:
        db.set_status(note["id"], new_status)
        changed = True

    if not changed:
        await react(context.bot, message.chat_id, message.message_id, REACTION_UNKNOWN)
        return

    updated = db.get_note(note["id"])
    emoji = REACTION_DONE if updated["status"] == "done" else type_reaction(updated["type"])
    await react(context.bot, message.chat_id, target.message_id, emoji)
    await react(context.bot, message.chat_id, message.message_id, REACTION_DONE)

    log.info(
        "Исправление №%s: %s/%s → %s/%s, статус %s",
        note["id"], note["type"], note["project"],
        updated["type"], updated["project"], updated["status"],
    )


def _keyboard(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
         for row in buttons]
    )


async def _handle_bro(update, context, text, context_text=None):
    message = update.effective_message
    await react(context.bot, message.chat_id, message.message_id, REACTION_PROCESSING)
    await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)

    try:
        answer, rows, action = await commands.answer(text, context_text=context_text)
    except Exception as e:
        log.exception("Ошибка обработки команды")
        await _reply(message, f"Сломался на этом запросе: {type(e).__name__}: {e}")
        await react(context.bot, message.chat_id, message.message_id, REACTION_UNKNOWN)
        return

    # Статус меняли словами («бро, удали эту запись») — реакции на самих
    # заметках должны это отразить, как при нажатии кнопки.
    if action:
        for note_id in action["ids"]:
            note = db.get_note(note_id)
            if note is None:
                continue
            if action["status"] == "done":
                emoji = REACTION_DONE
            elif action["status"] == "dropped":
                emoji = REACTION_UNKNOWN
            else:
                emoji = type_reaction(note["type"])
            await react(context.bot, note["chat_id"], note["message_id"], emoji)

    actionable = [r for r in rows if r["type"] == "task" and r["status"] in ("new", "kept")]
    keyboard = _keyboard(digest.task_buttons(actionable, limit=5))

    await _reply(message, answer, reply_markup=keyboard)
    await react(context.bot, message.chat_id, message.message_id, None)


# ---------------- Кнопки ----------------

def _strip_note_buttons(markup, note_id):
    if markup is None:
        return None
    suffix = f":{note_id}"
    rows = []
    for row in markup.inline_keyboard:
        keep = [b for b in row if not (b.callback_data or "").endswith(suffix)]
        if keep:
            rows.append(keep)
    return InlineKeyboardMarkup(rows) if rows else None


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return

    if OWNER_ID and query.from_user and query.from_user.id != OWNER_ID:
        await query.answer("Это не твой бот.", show_alert=True)
        return

    data = query.data or ""
    action, _, raw_id = data.partition(":")

    try:
        item_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    if action in ("done", "drop"):
        status = "done" if action == "done" else "dropped"
        db.set_status(item_id, status)
        note = db.get_note(item_id)
        if note is not None:
            await react(
                context.bot, note["chat_id"], note["message_id"],
                REACTION_DONE if status == "done" else REACTION_UNKNOWN,
            )
        await query.answer("Отметил сделанным" if status == "done" else "Выкинул")
        try:
            await query.edit_message_reply_markup(
                _strip_note_buttons(query.message.reply_markup, item_id)
            )
        except TelegramError:
            pass
        return

    if action in ("imp", "noimp"):
        path = _export_path(item_id)

        if action == "noimp":
            try:
                os.remove(path)
            except OSError:
                pass
            await query.answer("Отменил")
            try:
                await query.edit_message_text((query.message.text or "") + "\n\n✖ Импорт отменён.")
            except TelegramError:
                pass
            return

        if not os.path.isfile(path):
            await query.answer("Файл уже не найден — пришли экспорт заново.", show_alert=True)
            return

        if context.application.bot_data.get("import_running"):
            await query.answer("Импорт уже идёт, дождись его окончания.", show_alert=True)
            return

        try:
            with open(path, encoding="utf-8") as f:
                export = json.load(f)
        except Exception:
            await query.answer("Файл больше не читается.", show_alert=True)
            return

        chat_id = import_history.normalize_chat_id(export) or NOTES_CHAT_ID
        context.application.bot_data["import_running"] = True
        await query.answer("Запустил импорт")
        try:
            await query.edit_message_text((query.message.text or "") + "\n\n✔ Импортирую…")
        except TelegramError:
            pass

        context.application.create_task(
            _run_import(context.application, path, chat_id, query.message.chat_id)
        )
        return

    if action in ("merge", "nomerge"):
        group = db.get_dedup_group(item_id)
        if group is None or group["status"] != "proposed":
            await query.answer("Это предложение уже обработано.")
            return

        if action == "merge":
            ids = json.loads(group["note_ids"])
            notes = db.get_notes(ids)
            if notes:
                keep = max(notes, key=lambda r: r["date"])
                db.merge_notes(keep["id"], [r["id"] for r in notes if r["id"] != keep["id"]])
                for row in notes:
                    if row["id"] != keep["id"]:
                        await react(context.bot, row["chat_id"], row["message_id"], REACTION_UNKNOWN)
            db.set_dedup_status(item_id, "merged")
            await query.answer("Объединил")
            suffix = "\n\n✔ Объединено."
        else:
            db.set_dedup_status(item_id, "rejected")
            await query.answer("Оставил как есть")
            suffix = "\n\n✖ Оставил как есть."

        try:
            await query.edit_message_text((query.message.text or "") + suffix)
        except TelegramError:
            pass
        return

    if action in ("rule", "norule"):
        rule = db.get_rule(item_id)
        if rule is None or rule["status"] != "proposed":
            await query.answer("Это правило уже обработано.")
            return

        if action == "rule":
            db.set_rule_status(item_id, "active")
            await query.answer("Правило добавлено")
            suffix = "\n\n✔ Правило добавлено — буду его учитывать."
        else:
            db.set_rule_status(item_id, "rejected")
            await query.answer("Не добавляю")
            suffix = "\n\n✖ Не добавляю."

        try:
            await query.edit_message_text((query.message.text or "") + suffix)
        except TelegramError:
            pass
        return

    await query.answer()


# ---------------- Команды ----------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or not NOTES_CHAT_ID:
        await _setup_hint(update)
        return
    if not _allowed(update):
        return
    await _reply(update.effective_message, commands.help_text())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await _reply(update.effective_message, commands.stats_text())


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return

    rows = db.all_rules()
    if not rows:
        await _reply(update.effective_message, 
            "Правил пока нет. Они появятся сами, когда наберётся достаточно "
            "твоих исправлений — я предложу, ты подтвердишь."
        )
        return

    lines = []
    for row in rows:
        mark = {"active": "✔", "proposed": "…", "rejected": "✖"}.get(row["status"], "?")
        lines.append(f"{mark} №{row['id']} {row['text']}")
    await _reply(update.effective_message, "Мои правила:\n\n" + "\n".join(lines))


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск дайджеста — удобно для проверки, не дожидаясь утра."""
    if not _allowed(update):
        return
    await _reply(update.effective_message, "Собираю…")
    await _send_daily(context.application)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await _reply(update.effective_message, "Собираю недельный разбор, это займёт минуту…")
    await _send_weekly(context.application)


# ---------------- Дайджесты и расписание ----------------

async def _send_digest(application, messages):
    for item in messages:
        try:
            await _send(application.bot, 
                chat_id=_target_chat(),
                text=item["text"][:4000],
                reply_markup=_keyboard(item.get("buttons")),
            )
        except TelegramError as e:
            log.error("Не отправился дайджест: %s", e)
        await asyncio.sleep(0.5)  # чтобы не упереться во флуд-лимит


async def _send_daily(application):
    try:
        messages = digest.build_daily()
    except Exception:
        log.exception("Ошибка сборки дневного дайджеста")
        return
    await _send_digest(application, messages)


async def _send_weekly(application):
    try:
        messages = await digest.build_weekly()
        messages += await digest.propose_dedup()
        messages += await digest.propose_rules()
    except Exception:
        log.exception("Ошибка сборки недельного дайджеста")
        return
    await _send_digest(application, messages)


async def _scheduler(application):
    """Свой цикл вместо JobQueue: одна зависимость меньше, а логика «раз в
    сутки в такой-то час» тут элементарная. Отметки о запуске лежат в базе,
    поэтому перезапуск контейнера не приводит к повторной отправке."""
    log.info("Планировщик запущен (ежедневно в %02d:00, еженедельно в %02d:00)",
             DAILY_HOUR, WEEKLY_HOUR)

    while True:
        try:
            now = dt.datetime.now(TZ)

            if DAILY_ENABLED and now.hour == DAILY_HOUR:
                stamp = now.date().isoformat()
                if db.meta_get("last_daily") != stamp:
                    db.meta_set("last_daily", stamp)
                    log.info("Отправляю дневной дайджест")
                    await _send_daily(application)

            if WEEKLY_ENABLED and now.weekday() == WEEKLY_WEEKDAY and now.hour == WEEKLY_HOUR:
                year, week, _ = now.isocalendar()
                stamp = f"{year}-{week}"
                if db.meta_get("last_weekly") != stamp:
                    db.meta_set("last_weekly", stamp)
                    log.info("Отправляю недельный разбор")
                    await _send_weekly(application)
            await _sweep(application)
        except Exception:
            log.exception("Ошибка планировщика")

        await asyncio.sleep(60)


async def _post_init(application):
    application.bot_data["scheduler"] = asyncio.create_task(_scheduler(application))


async def _post_shutdown(application):
    task = application.bot_data.get("scheduler")
    if task:
        task.cancel()


async def on_error(update, context):
    # Conflict означает ровно одно: с этим токеном опрашивает Telegram ещё
    # кто-то. Трейсбек тут бесполезен, а причина всегда одна и та же —
    # поэтому пишем понятную строку вместо простыни.
    if isinstance(context.error, Conflict):
        log.error(
            "С этим токеном работает ещё один экземпляр бота — Telegram отдаёт "
            "обновления то одному, то другому. Обычно это не остановленный "
            "прошлый деплой, второй сервис Railway или бот, запущенный локально. "
            "Оставь ровно один."
        )
        return

    log.exception("Необработанная ошибка", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Не задан NOTES_BOT_TOKEN")

    db.init()
    log.info("База: %s", db.DB_PATH)

    if not OWNER_ID or not NOTES_CHAT_ID:
        log.warning(
            "NOTES_OWNER_ID/NOTES_CHAT_ID не заданы — бот работает в режиме "
            "настройки и подскажет нужные значения в ответ на любое сообщение."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler(["start", "help"], cmd_help))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("rules", cmd_rules))
    application.add_handler(CommandHandler("digest", cmd_digest))
    application.add_handler(CommandHandler("weekly", cmd_weekly))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageReactionHandler(on_reaction))
    application.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE & ~filters.COMMAND, on_edited)
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, on_message)
    )
    application.add_error_handler(on_error)

    log.info("Бот заметок запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
