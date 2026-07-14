# ---------------- Релей: MAX -> Telegram (всё) + VK (фото от админов) ----------------

import asyncio
import os
import time

import aiohttp
from telegram import Bot, InputMediaPhoto

import max_client
import vk_client
from relay_shops import RELAY_SHOPS

MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")
TG_BOT_TOKEN = os.environ.get("TOKENOTVET")

ADMIN_CACHE_TTL = 30 * 60  # секунд
_admin_cache: dict[int, tuple[float, set]] = {}


def _extract_text_and_photo_urls(message: dict) -> tuple[str, list[str]]:
    body = message.get("body", {})
    text = body.get("text") or ""

    photo_urls = [
        att["payload"]["url"]
        for att in body.get("attachments", [])
        if att.get("type") == "image" and att.get("payload", {}).get("url")
    ]

    return text, photo_urls


async def _get_admin_ids(session: aiohttp.ClientSession, chat_id: int) -> set:
    cached = _admin_cache.get(chat_id)
    if cached and time.monotonic() - cached[0] < ADMIN_CACHE_TTL:
        return cached[1]

    admin_ids = await max_client.get_admin_ids(session, MAX_BOT_TOKEN, chat_id)
    _admin_cache[chat_id] = (time.monotonic(), admin_ids)
    return admin_ids


async def _relay_to_telegram(bot: Bot, tg_chat_id: int, text: str, photos: list[bytes]) -> None:
    if not photos:
        if text:
            await bot.send_message(tg_chat_id, text)
        return

    if len(photos) == 1:
        await bot.send_photo(tg_chat_id, photo=photos[0], caption=text or None)
        return

    media = [InputMediaPhoto(p, caption=text if i == 0 else None) for i, p in enumerate(photos)]
    await bot.send_media_group(tg_chat_id, media)


async def _handle_message(
    session: aiohttp.ClientSession,
    bot: Bot,
    shop: dict,
    message: dict,
    chat_id: int,
) -> None:
    text, photo_urls = _extract_text_and_photo_urls(message)
    if not text and not photo_urls:
        return

    photos = [
        await max_client.download_attachment(session, MAX_BOT_TOKEN, url)
        for url in photo_urls
    ]

    try:
        await _relay_to_telegram(bot, shop["tg_chat_id"], text, photos)
    except Exception as e:
        print(f"⚠️ Ошибка отправки в Telegram ({shop['name']}): {e}")

    if not photos:
        return

    sender_id = message.get("sender", {}).get("userId")
    if sender_id is None:
        return

    admin_ids = await _get_admin_ids(session, chat_id)
    if sender_id not in admin_ids:
        return

    vk_token = os.environ.get(shop["vk_token_env"])
    if not vk_token or not shop["vk_group_id"]:
        return

    try:
        await vk_client.post_to_wall(session, vk_token, shop["vk_group_id"], text, photos)
    except Exception as e:
        print(f"⚠️ Ошибка публикации в VK ({shop['name']}): {e}")


async def relay_loop() -> None:
    if not MAX_BOT_TOKEN or not TG_BOT_TOKEN:
        print("⚠️ MAX_BOT_TOKEN или TOKENOTVET не заданы — bot3 не запущен")
        return

    print("🚀 bot3 запущен, начинаю polling MAX /updates...")
    marker = None

    async with aiohttp.ClientSession() as session, Bot(TG_BOT_TOKEN) as bot:
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
