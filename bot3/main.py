# ---------------- Релей: MAX <-> Telegram + VK (фото от админов) ----------------
# Плюс автоответчик и антиспам-модерация внутри самого MAX (переиспользуют
# логику и тексты bot2 — см. sys.path ниже).

import asyncio
import os
import re
import sys
import time

import aiohttp
from telegram import Bot, InputMediaPhoto, InputMediaVideo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot2"))

import max_client
import vk_client
import relay_state
from relay_shops import RELAY_SHOPS, TEST_CHAT_IDS
from auto_reply import (
    ADDRESS_KEYWORDS, WORK_KEYWORDS, OTHER_SHOP_KEYWORDS, TG_LINK_KEYWORDS,
    INVITE_KEYWORDS, OTHER_SHOP_TEXT, invite_text, is_relevant,
    is_blacklisted_link, CONFIRM_QUESTIONS,
)
from shops import SHOPS
from moderation import check_spam, confirm_intent, ocr_image, TEST_NON_ADMIN_TAG

MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")
TG_BOT_TOKEN = os.environ.get("TOKENOTVET")

# id самого MAX-бота — определяется при старте, нужен для защиты от
# зацикливания (TG -> MAX -> TG), т.к. MAX отдаёт в /updates и его же
# собственные сообщения.
MAX_BOT_USER_ID = None

ADMIN_CACHE_TTL = 30 * 60  # секунд
_admin_cache: dict[int, tuple[float, set]] = {}


def _extract_text_and_media(message: dict) -> tuple[str, list[str], list[str]]:
    """Возвращает (text, photo_urls, video_urls)."""
    body = message.get("body", {})
    text = body.get("text") or ""
    attachments = body.get("attachments", [])

    # Пересланное сообщение (форвард) хранит своё содержимое не в body,
    # а во вложенном link.message — сама body у форварда обычно пустая.
    link = message.get("link") or {}
    if link.get("type") == "forward":
        fwd_body = link.get("message") or {}
        text = text or fwd_body.get("text") or ""
        attachments = attachments or fwd_body.get("attachments", [])

    def urls(*att_types: str) -> list[str]:
        return [
            att["payload"]["url"]
            for att in attachments
            if att.get("type") in att_types and att.get("payload", {}).get("url")
        ]

    # MAX не единообразен: фото встречаются то как "image", то как "photo".
    return text, urls("image", "photo"), urls("video")


async def _get_admin_ids(session: aiohttp.ClientSession, chat_id: int) -> set:
    cached = _admin_cache.get(chat_id)
    if cached and time.monotonic() - cached[0] < ADMIN_CACHE_TTL:
        return cached[1]

    admin_ids = await max_client.get_admin_ids(session, MAX_BOT_TOKEN, chat_id)
    _admin_cache[chat_id] = (time.monotonic(), admin_ids)
    return admin_ids


def _sender_name(sender: dict) -> str:
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])).strip()
    return name or sender.get("username") or "MAX-пользователь"


def _format_relayed_text(text: str, author_name: str, is_admin: bool, source_label: str) -> str:
    """Админов не подписываем, остальных — "MAX, Имя: текст"."""
    if is_admin:
        return text
    prefix = f"{source_label}, {author_name}"
    return f"{prefix}: {text}" if text else prefix


def _max_join_url(shop_content: dict) -> str | None:
    """Достаёт ссылку-приглашение в MAX-группу из markdown-текста max_link
    (в shops.py она хранится в виде "[нажмите сюда](https://max.ru/join/...)")."""
    match = re.search(r"https://max\.ru/join/\S+?(?=[)\s]|$)", shop_content.get("max_link", ""))
    return match.group(0) if match else None


async def _max_auto_reply_text(text: str, tg_chat_id: int) -> str | None:
    """Ответ на вопрос об адресе/графике — прямо в MAX. Переиспользует ключевые
    слова и тексты магазинов из bot2 (вопрос "есть ли вы в MAX" здесь не имеет
    смысла — пользователь и так пишет из MAX, поэтому не отвечаем на него)."""
    if not text or is_blacklisted_link(text):
        return None

    shop_content = SHOPS.get(tg_chat_id)
    if not shop_content:
        return None

    # Приглашение шлём в MAX-чат — человек уже здесь, добавить хочет сюда же.
    invite_link = _max_join_url(shop_content) or shop_content["tg_link"]

    for category, keywords, reply in (
        ("other_shop", OTHER_SHOP_KEYWORDS, OTHER_SHOP_TEXT),
        ("invite", INVITE_KEYWORDS, invite_text(invite_link)),
        ("address", ADDRESS_KEYWORDS, shop_content["address"]),
        ("work_time", WORK_KEYWORDS, shop_content["work_time"]),
        ("tg_link", TG_LINK_KEYWORDS, f"📱 Наш чат в Telegram: {shop_content['tg_link']}"),
    ):
        if not is_relevant(text, keywords):
            continue
        if await confirm_intent(text, CONFIRM_QUESTIONS[category]):
            return reply

    return None


async def _remove_max_message(
    session: aiohttp.ClientSession,
    chat_id: int,
    message: dict,
    sender_id,
    ban: bool,
) -> None:
    """Удаляет сообщение и (если ban=True) банит автора."""
    message_id = message.get("body", {}).get("mid")
    if message_id:
        try:
            await max_client.delete_message(session, MAX_BOT_TOKEN, message_id)
        except Exception as e:
            print(f"⚠️ Не удалось удалить сообщение в MAX-чате {chat_id}: {e}")

    if ban and sender_id is not None:
        try:
            await max_client.ban_member(session, MAX_BOT_TOKEN, chat_id, sender_id)
        except Exception as e:
            print(f"⚠️ Не удалось забанить {sender_id} в MAX-чате {chat_id}: {e}")

    label = "Забанен" if ban else "Удалено (без бана)"
    print(f"🚫 {label} в MAX-чате {chat_id}: user_id={sender_id}")


TG_CAPTION_LIMIT = 1024  # лимит Telegram на подпись к фото (у обычного текста лимит больше — 4096)


async def _relay_to_telegram(
    bot: Bot,
    tg_chat_id: int,
    text: str,
    photos: list[bytes],
    videos: list[bytes] | None = None,
    reply_to_message_id: int | None = None,
) -> int | None:
    """Отправляет сообщение, возвращает message_id (для reply-threading)."""
    videos = videos or []
    combined = [("photo", p) for p in photos] + [("video", v) for v in videos]

    if not combined:
        if text:
            msg = await bot.send_message(tg_chat_id, text, reply_to_message_id=reply_to_message_id)
            return msg.message_id
        return None

    # Длинный текст не влезает в подпись к фото/видео — шлём медиа без подписи,
    # а текст отдельным сообщением следом (тем же тредом).
    fits_caption = len(text) <= TG_CAPTION_LIMIT
    caption = text if fits_caption else None

    if len(combined) == 1:
        kind, data = combined[0]
        if kind == "photo":
            msg = await bot.send_photo(
                tg_chat_id, photo=data, caption=caption or None,
                reply_to_message_id=reply_to_message_id,
            )
        else:
            msg = await bot.send_video(
                tg_chat_id, video=data, caption=caption or None,
                reply_to_message_id=reply_to_message_id,
            )
    else:
        media = [
            (InputMediaPhoto if kind == "photo" else InputMediaVideo)(data, caption=caption if i == 0 else None)
            for i, (kind, data) in enumerate(combined)
        ]
        messages = await bot.send_media_group(tg_chat_id, media, reply_to_message_id=reply_to_message_id)
        msg = messages[0] if messages else None

    message_id = msg.message_id if msg else None

    if not fits_caption and text:
        follow_up = await bot.send_message(tg_chat_id, text, reply_to_message_id=message_id)
        message_id = message_id or follow_up.message_id

    return message_id


async def _handle_message(
    session: aiohttp.ClientSession,
    bot: Bot,
    shop: dict,
    message: dict,
    chat_id: int,
) -> None:
    text, photo_urls, video_urls = _extract_text_and_media(message)
    if not text and not photo_urls and not video_urls:
        link = message.get("link") or {}
        if link:
            print(f"🔗 Пустое сообщение с link (возможно, форвард нераспознанного формата): {message}")
        return

    sender = message.get("sender") or {}
    sender_id = sender.get("user_id")

    # Не реагируем на собственные сообщения бота (иначе TG -> MAX -> TG зациклится)
    if sender_id is not None and sender_id == MAX_BOT_USER_ID:
        return

    admin_ids = await _get_admin_ids(session, chat_id)
    is_admin = sender_id in admin_ids if sender_id is not None else False
    is_test_chat = chat_id in TEST_CHAT_IDS
    if TEST_NON_ADMIN_TAG in text or is_test_chat:
        is_admin = False

    photos = [
        await max_client.download_attachment(session, MAX_BOT_TOKEN, url)
        for url in photo_urls
    ]
    videos = [
        await max_client.download_attachment(session, MAX_BOT_TOKEN, url)
        for url in video_urls
    ]

    # Модерация идёт ДО автоответа: иначе спам, в котором есть вопрос про
    # адрес/график, получал бы автоответ и уходил от проверки совсем.
    if not is_admin:
        # Явный текстовый спам (человек сам написал) — удаляем и баним.
        # Реклама, найденная только на картинке через OCR, — точность ниже,
        # поэтому только удаляем, без бана.
        removed = False

        if text:
            if await check_spam(text) == "BAN":
                # В тест-чате не баним, чтобы не залочить себя при тестах.
                await _remove_max_message(session, chat_id, message, sender_id, ban=not is_test_chat)
                removed = True

        if not removed and photos:
            # Проверяем ВСЕ картинки: в альбоме реклама может быть не первой.
            ocr_chunks = []
            for photo in photos:
                chunk = await asyncio.to_thread(ocr_image, photo)
                if chunk:
                    ocr_chunks.append(chunk)

            if ocr_chunks:
                combined_text = "\n".join([text] + ocr_chunks) if text else "\n".join(ocr_chunks)
                if await check_spam(combined_text) == "BAN":
                    await _remove_max_message(session, chat_id, message, sender_id, ban=False)
                    removed = True

        if removed:
            return

    # У песочницы своего магазина нет — тексты автоответов берём у указанного.
    content_chat_id = shop.get("content_tg_chat_id") or shop["tg_chat_id"]
    reply_text = await _max_auto_reply_text(text, content_chat_id)
    if reply_text:
        try:
            await max_client.send_message(
                session, MAX_BOT_TOKEN, chat_id, reply_text,
                reply_to_mid=message.get("body", {}).get("mid"),
            )
        except Exception as e:
            print(f"⚠️ Ошибка автоответа в MAX ({shop['name']}): {e}")
        # Вопрос уже отвечен на месте — дальше (в TG/VK) не пересылаем
        return

    # Нет парного TG-чата (например, тестовая песочница) — модерация уже
    # отработала выше, а релеить/постить в VK некуда.
    if not shop["tg_chat_id"]:
        return

    relay_text = _format_relayed_text(text, _sender_name(sender), is_admin, "MAX")

    # Если это ответ на ранее пересланное из TG сообщение — отвечаем в TG тем же тредом
    reply_to_tg_message_id = None
    link = message.get("link") or {}
    if link:
        print(f"🔗 Сообщение содержит link: {link}")
    if link.get("type") == "reply":
        replied_mid = link.get("message", {}).get("mid") or link.get("mid")
        found = relay_state.find_tg_by_max_mid(replied_mid)
        print(f"🔎 Reply на mid={replied_mid}, найдено в relay_state: {found}")
        if found:
            _, reply_to_tg_message_id = found

    try:
        tg_message_id = await _relay_to_telegram(
            bot, shop["tg_chat_id"], relay_text, photos, videos,
            reply_to_message_id=reply_to_tg_message_id,
        )
        max_mid = message.get("body", {}).get("mid")
        print(f"💾 Сохраняю маппинг: max_mid={max_mid} <-> tg_message_id={tg_message_id}")
        if tg_message_id and max_mid:
            relay_state.save_mapping(max_mid, chat_id, tg_message_id, shop["tg_chat_id"])
    except Exception as e:
        print(f"⚠️ Ошибка отправки в Telegram ({shop['name']}): {e}")

    if not photos or not is_admin:
        return

    vk_token = os.environ.get(shop["vk_token_env"])
    if not vk_token or not shop["vk_group_id"]:
        print(f"⚠️ Не настроен VK для «{shop['name']}» (токен есть: {bool(vk_token)}, group_id: {shop['vk_group_id']})")
        return

    try:
        await vk_client.post_to_wall(session, vk_token, shop["vk_group_id"], text, photos)
    except Exception as e:
        print(f"⚠️ Ошибка публикации в VK ({shop['name']}): {e}")


async def relay_loop() -> None:
    global MAX_BOT_USER_ID

    if not MAX_BOT_TOKEN or not TG_BOT_TOKEN:
        print("⚠️ MAX_BOT_TOKEN или TOKENOTVET не заданы — bot3 не запущен")
        return

    print("🚀 bot3 запущен, начинаю polling MAX /updates...")
    marker = None

    async with aiohttp.ClientSession() as session, Bot(TG_BOT_TOKEN) as bot:
        try:
            me = await max_client.get_me(session, MAX_BOT_TOKEN)
            MAX_BOT_USER_ID = me.get("user_id")
            print(f"🤖 MAX-бот: {me.get('first_name')} (user_id={MAX_BOT_USER_ID})")
        except Exception as e:
            print(f"⚠️ Не удалось определить свой user_id в MAX (GET /me): {e}")

        while True:
            try:
                data = await max_client.get_updates(session, MAX_BOT_TOKEN, marker)
            except Exception as e:
                print(f"⚠️ Ошибка получения обновлений MAX (возможно, неверный MAX_BOT_TOKEN): {e}")
                await asyncio.sleep(5)
                continue

            marker = data.get("marker", marker)
            updates = data.get("updates", [])
            if updates:
                print(f"📩 Получено обновлений: {len(updates)}")

            for update in updates:
                message = max_client.extract_message(update)
                if not message:
                    print(f"🔎 Пропущено (не message_created или неожиданный формат): {update}")
                    continue

                chat_id = message.get("recipient", {}).get("chat_id")
                shop = RELAY_SHOPS.get(chat_id)
                if not shop:
                    print(f"⚠️ Неизвестный MAX-чат: {chat_id}")
                    continue

                try:
                    await _handle_message(session, bot, shop, message, chat_id)
                    print(f"✅ Сообщение из «{shop['name']}» обработано")
                except Exception as e:
                    print(f"⚠️ Ошибка обработки сообщения ({shop['name']}): {e}")


if __name__ == "__main__":
    asyncio.run(relay_loop())
