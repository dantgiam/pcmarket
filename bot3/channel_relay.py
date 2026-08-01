# ---------------- Релей постов MAX-канала в Telegram ----------------
# В отличие от RELAY_SHOPS (двусторонний релей магазин-чатов с модерацией
# и VK) — здесь односторонняя копия постов канала в TG: без модерации и
# без VK (это собственный канал владельца, не чат с гостями), зато с
# сохранением форматирования (жирный/курсив/цитаты/ссылки) и всех фото/видео.
#
# Ключ CHANNEL_SOURCES — числовой chat_id канала (как и в RELAY_SHOPS), а не
# публичная ссылка: GET /chats/{chatLink} у MAX не резолвит некоторые каналы
# по ссылке даже когда бот уже в них состоит (404), поэтому id надёжнее брать
# напрямую — например, из ссылки вида https://web.max.ru/<chat_id>. Бот должен
# быть добавлен администратором канала в MAX — иначе GET /messages отдаёт 403.
#
# Модуль используется и из bot3 (автоматически: бэкафилл истории при
# старте + релей новых постов через /updates), и из bot2 (команда
# /copy_channel — ручной перезапуск копирования по требованию, без
# рестарта всего контейнера).

import asyncio
import os

import aiohttp
from telegram import Bot, InputMediaPhoto, InputMediaVideo, MessageEntity

import max_client
import relay_state

MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")

CHANNEL_SOURCES = {
    -73758710575792: {
        "tg_chat_id": -1004462241776,
        "name": "Канал Адыгид (MAX)",
    },
}

TG_CAPTION_LIMIT = 1024  # лимит Telegram на подпись к фото/видео (у обычного текста лимит больше — 4096)

# MAX markup -> Telegram MessageEntity.type. В Telegram нет аналога для
# heading/highlighted — берём bold как ближайшее визуально.
MARKUP_TO_TG = {
    "strong": MessageEntity.BOLD,
    "emphasized": MessageEntity.ITALIC,
    "monospaced": MessageEntity.CODE,
    "strikethrough": MessageEntity.STRIKETHROUGH,
    "underline": MessageEntity.UNDERLINE,
    "heading": MessageEntity.BOLD,
    "highlighted": MessageEntity.BOLD,
    # "blockquote" появился в Bot API 7.4 (апрель 2024), но константы
    # MessageEntity.BLOCKQUOTE в python-telegram-bot==20.7 ещё нет — берём
    # сырую строку, PTB её просто передаёт дальше без валидации.
    "quote": "blockquote",
}


def extract_text_and_media(message: dict) -> tuple[str, list[str], list[str], list[dict]]:
    """Возвращает (text, photo_urls, video_urls, markup)."""
    body = message.get("body", {})
    text = body.get("text") or ""
    attachments = body.get("attachments", [])
    markup = body.get("markup") or []

    # Пересланное сообщение (форвард) хранит своё содержимое не в body,
    # а во вложенном link.message — сама body у форварда обычно пустая.
    link = message.get("link") or {}
    if link.get("type") == "forward":
        fwd_body = link.get("message") or {}
        text = text or fwd_body.get("text") or ""
        attachments = attachments or fwd_body.get("attachments", [])
        markup = markup or (fwd_body.get("markup") or [])

    def urls(*att_types: str) -> list[str]:
        return [
            att["payload"]["url"]
            for att in attachments
            if att.get("type") in att_types and att.get("payload", {}).get("url")
        ]

    # MAX не единообразен: фото встречаются то как "image", то как "photo".
    return text, urls("image", "photo"), urls("video"), markup


def convert_markup(markup: list[dict] | None) -> list[MessageEntity]:
    """Конвертирует markup поста MAX в MessageEntity Telegram, чтобы форматирование
    (жирный/курсив/цитаты/ссылки) доехало без ручного markdown-экранирования.
    from/length у MAX — оффсеты по UTF-16 code units, как и entities в Telegram,
    поэтому передаём их как есть, без пересчёта."""
    entities = []
    for el in markup or []:
        offset, length = el.get("from"), el.get("length")
        if offset is None or length is None:
            continue

        if el.get("type") == "link":
            url = el.get("url")
            if url:
                entities.append(MessageEntity(MessageEntity.TEXT_LINK, offset, length, url=url))
            continue

        tg_type = MARKUP_TO_TG.get(el.get("type"))
        if tg_type:
            entities.append(MessageEntity(tg_type, offset, length))

    return entities


async def relay_to_telegram(
    bot: Bot,
    tg_chat_id: int,
    text: str,
    photos: list[bytes],
    videos: list[bytes] | None = None,
    reply_to_message_id: int | None = None,
    entities: list[MessageEntity] | None = None,
) -> int | None:
    """Отправляет сообщение, возвращает message_id (для reply-threading).
    entities — форматирование (bold/italic/quote/...), применяется к тексту
    целиком: он не режется, поэтому корректно и в подписи к фото/видео, и
    в отдельном сообщении."""
    videos = videos or []
    combined = [("photo", p) for p in photos] + [("video", v) for v in videos]

    if not combined:
        if text:
            msg = await bot.send_message(
                tg_chat_id, text, reply_to_message_id=reply_to_message_id, entities=entities,
            )
            return msg.message_id
        return None

    # Длинный текст не влезает в подпись к фото/видео — шлём медиа без подписи,
    # а текст отдельным сообщением следом (тем же тредом).
    fits_caption = len(text) <= TG_CAPTION_LIMIT
    caption = text if fits_caption else None
    caption_entities = entities if fits_caption else None

    if len(combined) == 1:
        kind, data = combined[0]
        if kind == "photo":
            msg = await bot.send_photo(
                tg_chat_id, photo=data, caption=caption or None, caption_entities=caption_entities,
                reply_to_message_id=reply_to_message_id,
            )
        else:
            msg = await bot.send_video(
                tg_chat_id, video=data, caption=caption or None, caption_entities=caption_entities,
                reply_to_message_id=reply_to_message_id,
            )
    else:
        media = [
            (InputMediaPhoto if kind == "photo" else InputMediaVideo)(
                data,
                caption=caption if i == 0 else None,
                caption_entities=caption_entities if i == 0 else None,
            )
            for i, (kind, data) in enumerate(combined)
        ]
        messages = await bot.send_media_group(tg_chat_id, media, reply_to_message_id=reply_to_message_id)
        msg = messages[0] if messages else None

    message_id = msg.message_id if msg else None

    if not fits_caption and text:
        follow_up = await bot.send_message(
            tg_chat_id, text, reply_to_message_id=message_id, entities=entities,
        )
        message_id = message_id or follow_up.message_id

    return message_id


async def handle_channel_message(
    session: aiohttp.ClientSession,
    bot: Bot,
    cfg: dict,
    message: dict,
    chat_id: int,
) -> None:
    """Копирует один пост канала в TG: без модерации/VK, зато с форматированием.
    Идемпотентно — уже скопированные посты (по max_mid) пропускаются, это
    защищает от повторной пересылки при пересечении бэкафилла, /updates и
    повторного запуска команды /copy_channel."""
    max_mid = message.get("body", {}).get("mid")
    if max_mid and relay_state.find_tg_by_max_mid(max_mid):
        return

    text, photo_urls, video_urls, markup = extract_text_and_media(message)
    if not text and not photo_urls and not video_urls:
        return

    photos = [await max_client.download_attachment(session, MAX_BOT_TOKEN, url) for url in photo_urls]
    videos = [await max_client.download_attachment(session, MAX_BOT_TOKEN, url) for url in video_urls]
    entities = convert_markup(markup)

    try:
        tg_message_id = await relay_to_telegram(
            bot, cfg["tg_chat_id"], text, photos, videos, entities=entities,
        )
        if tg_message_id and max_mid:
            relay_state.save_mapping(max_mid, chat_id, tg_message_id, cfg["tg_chat_id"])
    except Exception as e:
        print(f"⚠️ Ошибка копирования поста канала «{cfg['name']}» в TG: {e}")


async def backfill_channel(session: aiohttp.ClientSession, bot: Bot, chat_id: int, cfg: dict) -> int:
    """Копирует всю уже существующую историю канала. Безопасно перезапускать —
    уже скопированные посты пропускаются в handle_channel_message по max_mid.
    Пагинация — через `to` (в MAX нет marker для истории сообщений).
    Возвращает количество найденных сообщений (включая уже скопированные)."""
    messages = []
    to = None
    while True:
        try:
            data = await max_client.get_messages(session, MAX_BOT_TOKEN, chat_id, count=100, to=to)
        except Exception as e:
            print(f"⚠️ Ошибка получения истории канала «{cfg['name']}»: {e}")
            break

        batch = data.get("messages", [])
        if not batch:
            break

        messages.extend(batch)
        if len(batch) < 100:
            break
        to = min(m["timestamp"] for m in batch) - 1

    messages.sort(key=lambda m: m.get("timestamp", 0))
    print(f"📜 Бэкафилл канала «{cfg['name']}»: найдено {len(messages)} сообщений")

    for message in messages:
        try:
            await handle_channel_message(session, bot, cfg, message, chat_id)
        except Exception as e:
            print(f"⚠️ Ошибка бэкафилла поста канала «{cfg['name']}»: {e}")
        # Пауза между постами, чтобы не словить flood-control Telegram при
        # массовой заливке истории (не нужна для отдельных живых постов).
        await asyncio.sleep(1.5)

    return len(messages)


async def resolve_channel_sources(session: aiohttp.ClientSession) -> dict:
    """Проверяет, что бот действительно активный участник каждого канала из
    CHANNEL_SOURCES (GET /chats/{chatId} по числовому id — надёжнее, чем поиск
    по публичной ссылке). Возвращает {chat_id: cfg} только для доступных каналов."""
    resolved = {}
    for chat_id, cfg in CHANNEL_SOURCES.items():
        try:
            chat = await max_client.get_chat(session, MAX_BOT_TOKEN, chat_id)
        except Exception as e:
            print(f"⚠️ Не удалось получить канал «{cfg['name']}» (chat_id={chat_id}): {e}")
            continue

        status = chat.get("status")
        if status != "active":
            print(
                f"⚠️ Канал «{cfg['name']}» (chat_id={chat_id}) найден, но бот не активный участник "
                f"(status={status}). Добавьте бота администратором канала в MAX, иначе посты "
                "недоступны (403)."
            )
            continue

        resolved[chat_id] = cfg
        print(f"🔗 Канал «{cfg['name']}» (chat_id={chat_id}) доступен, статус={status}")

    return resolved
