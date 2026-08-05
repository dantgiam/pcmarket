import asyncio
import os
import sys
import time

import aiohttp
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

from auto_reply import handle_auto_reply
from moderation import check_spam, ocr_image, TEST_NON_ADMIN_TAG
from shops import contains_own_link

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot3"))
import max_client
import relay_state
import channel_relay
from relay_shops import RELAY_SHOPS

# Обратная карта: tg_chat_id -> max_chat_id, для релея TG -> MAX
MAX_CHAT_MAP = {shop["tg_chat_id"]: max_chat_id for max_chat_id, shop in RELAY_SHOPS.items()}

TOKEN = os.environ.get("TOKENOTVET")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")

# Чат, куда падают логи удалённых сообщений. ID через env.
_log = os.environ.get("LOG_CHAT_ID")
LOG_CHAT_ID = int(_log) if _log else None

# Администраторы, чьи сообщения не модерируются
ADMIN_IDS = [1014380197, 866973179]


def user_label(user) -> str:
    """Читаемое описание пользователя для лога."""
    parts = [user.full_name or "—"]
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"[id {user.id}]")
    return " ".join(parts)


async def is_exempt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True, если сообщение от админа чата или из белого списка — не модерируем."""
    msg = update.effective_message
    user = update.effective_user

    test_text = (msg.text or msg.caption or "") if msg else ""
    if TEST_NON_ADMIN_TAG in test_text:
        return False

    # Сообщение от имени чата/канала (sender_chat). Анонимный админ пишет от
    # имени самой группы — это админ. Всё остальное (пост от имени стороннего
    # канала) — частый способ обойти модерацию, поэтому проверяем.
    sender_chat = msg.sender_chat if msg else None
    if sender_chat is not None:
        return sender_chat.id == update.effective_chat.id

    if user is None:
        return True

    if user.id in ADMIN_IDS:
        return True

    try:
        member = await context.bot.get_chat_member(
            update.effective_chat.id, user.id
        )
        if member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            return True
    except Exception:
        pass

    return False


async def send_removal_log(context, chat, user, text: str, sender_chat=None):
    """Отправляет в чат-логов запись об удалённом сообщении."""
    if not LOG_CHAT_ID:
        return

    if sender_chat is not None:
        author = f"канал «{sender_chat.title or sender_chat.id}» [id {sender_chat.id}]"
    else:
        author = user_label(user) if user else "—"

    log_text = (
        "🗑️ Удалено сообщение (спам)\n\n"
        f"👤 {author}\n"
        f"💬 Чат: {chat.title or chat.id}\n\n"
        f"📝 Сообщение:\n{text}"
    )

    try:
        await context.bot.send_message(LOG_CHAT_ID, log_text)
    except Exception:
        pass


async def message_image_bytes(msg, context) -> bytes | None:
    """Картинка из сообщения — обычным фото или файлом (несжатая картинка
    документом — частый способ протащить рекламный баннер мимо проверки)."""
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id

    if not file_id:
        return None

    try:
        file = await context.bot.get_file(file_id)
        return bytes(await file.download_as_bytearray())
    except Exception as e:
        print(f"⚠️ Не удалось скачать картинку для проверки: {type(e).__name__}: {e}")
        return None


# Альбомы, признанные спамом: media_group_id -> время. Остальные картинки
# альбома приходят отдельными апдейтами уже после удаления первой, и без
# этого списка они остались бы висеть в чате.
_spam_media_groups: dict[str, float] = {}
MEDIA_GROUP_TTL = 5 * 60


def remember_spam_album(msg) -> None:
    if not msg.media_group_id:
        return
    now = time.monotonic()
    for group_id, seen_at in list(_spam_media_groups.items()):
        if now - seen_at > MEDIA_GROUP_TTL:
            _spam_media_groups.pop(group_id, None)
    _spam_media_groups[msg.media_group_id] = now


def is_spam_album(msg) -> bool:
    if not msg.media_group_id:
        return False
    seen_at = _spam_media_groups.get(msg.media_group_id)
    if seen_at is None:
        return False
    if time.monotonic() - seen_at > MEDIA_GROUP_TTL:
        _spam_media_groups.pop(msg.media_group_id, None)
        return False
    return True


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет сообщение на спам через DeepSeek. Бот никогда не банит —
    нарушение только удаляется.
    Возвращает True, если сообщение было удалено (не нужно релеить в MAX)."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    sender_chat = msg.sender_chat

    # Остальные картинки альбома, первая часть которого уже признана спамом.
    if is_spam_album(msg):
        try:
            await msg.delete()
        except Exception:
            pass
        return True

    typed_text = msg.text or msg.caption or ""

    # Ссылка на одну из НАШИХ групп/сайт — не спам, даже если её кинул не
    # админ (например, в ответ на просьбу другого участника добавить в чат).
    if contains_own_link(typed_text):
        return False

    if typed_text:
        if await check_spam(typed_text) == "BAN":
            try:
                await msg.delete()
            except Exception:
                pass
            remember_spam_album(msg)
            await send_removal_log(context, chat, user, typed_text, sender_chat)
            return True

    photo_bytes = await message_image_bytes(msg, context)
    if not photo_bytes:
        return False

    ocr_text = await asyncio.to_thread(ocr_image, photo_bytes)
    if not ocr_text:
        return False

    combined_text = f"{typed_text}\n{ocr_text}".strip() if typed_text else ocr_text
    if await check_spam(combined_text) != "BAN":
        return False

    try:
        await msg.delete()
    except Exception:
        pass
    remember_spam_album(msg)

    print(f"🗑️ Удалено рекламное фото: чат={chat.id}, user={user.id if user else '?'}")
    return True


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ID текущего чата — чтобы узнать LOG_CHAT_ID."""
    await update.message.reply_text(f"🆔 Chat ID: {update.effective_chat.id}")


async def copy_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск копирования постов MAX-канала в TG (см. bot3/channel_relay.py).
    Обычно копирование уже идёт автоматически при старте bot3 — команда нужна,
    чтобы прогнать его на лету (например, повторно после ошибки), не передёргивая
    весь контейнер. Безопасно вызывать многократно: уже скопированные посты
    пропускаются по max_mid."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        return

    if not MAX_BOT_TOKEN:
        await update.message.reply_text("⚠️ MAX_BOT_TOKEN не задан")
        return

    await update.message.reply_text("⏳ Ищу канал и копирую посты в TG...")

    async with aiohttp.ClientSession() as session:
        resolved = await channel_relay.resolve_channel_sources(session)
        if not resolved:
            await update.message.reply_text(
                "⚠️ Канал не найден или бот не администратор там (подробности в логах)."
            )
            return

        total = 0
        for chat_id, cfg in resolved.items():
            total += await channel_relay.backfill_channel(session, context.bot, chat_id, cfg)

    await update.message.reply_text(f"✅ Готово: обработано {total} постов канала.")


async def relay_to_max(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin: bool) -> None:
    """Пересылает сообщение из TG-группы в связанный MAX-чат (bot3/relay_shops.py).
    Админов не подписываем, остальных — "TG, Имя: текст"."""
    if not MAX_BOT_TOKEN:
        return

    max_chat_id = MAX_CHAT_MAP.get(update.effective_chat.id)
    if not max_chat_id:
        return

    text = update.message.text or update.message.caption or ""

    # Нечего пересылать: файл/гифка/видео без подписи ушли бы в MAX пустой
    # строкой «TG, Имя» (сами вложения таких типов мы туда не заливаем).
    if not text and not update.message.photo:
        return

    author_name = update.effective_user.full_name if update.effective_user else "TG-пользователь"
    if is_admin:
        relay_text = text
    else:
        prefix = f"TG, {author_name}"
        relay_text = f"{prefix}: {text}" if text else prefix

    # Если это ответ на ранее пересланное из MAX сообщение — отвечаем в MAX тем же тредом
    reply_to_mid = None
    if update.message.reply_to_message:
        found = relay_state.find_max_by_tg_message(
            update.effective_chat.id, update.message.reply_to_message.message_id
        )
        print(f"🔎 TG reply на message_id={update.message.reply_to_message.message_id}, найдено в relay_state: {found}")
        if found:
            _, reply_to_mid = found

    try:
        async with aiohttp.ClientSession() as session:
            attachments = None
            if update.message.photo:
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                photo_bytes = bytes(await file.download_as_bytearray())
                token = await max_client.upload_image(session, MAX_BOT_TOKEN, photo_bytes)
                attachments = [{"type": "image", "payload": {"token": token}}]

            if relay_text or attachments:
                max_mid = await max_client.send_message(
                    session, MAX_BOT_TOKEN, max_chat_id, relay_text, attachments,
                    reply_to_mid=reply_to_mid,
                )
                print(f"💾 Сохраняю маппинг: max_mid={max_mid} <-> tg_message_id={update.message.message_id}")
                if max_mid:
                    relay_state.save_mapping(
                        max_mid, max_chat_id, update.message.message_id, update.effective_chat.id
                    )
    except Exception as e:
        print(f"⚠️ Ошибка релея в MAX: {e}")


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # В самом чате-логов ничего не модерируем
    if LOG_CHAT_ID and update.effective_chat.id == LOG_CHAT_ID:
        return

    # Админов и доверенных не модерируем, их сообщения в MAX идут без подписи
    exempt = await is_exempt(update, context)

    # Модерация идёт ДО автоответа: иначе спам, в котором есть вопрос про
    # адрес/график, получал бы автоответ и уходил от проверки совсем.
    if not exempt and await moderate(update, context):
        return

    # Обычный вопрос клиента (адрес / график / MAX) — отвечаем и не пересылаем
    if await handle_auto_reply(update, context):
        return

    await relay_to_max(update, context, is_admin=exempt)


async def handle_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отредактированные сообщения — классический обход модерации: прислать
    безобидный текст, потом заменить его на рекламу. Проверяем повторно
    (только модерация, релей и автоответ тут не нужны)."""
    if LOG_CHAT_ID and update.effective_chat.id == LOG_CHAT_ID:
        return

    if not await is_exempt(update, context):
        await moderate(update, context)


# Реклама приходит не только текстом и фото: картинку часто шлют файлом
# (несжатой), а также GIF-анимацией — раньше такие сообщения не попадали
# в обработчик вообще и проходили мимо модерации.
CONTENT_FILTER = (
    filters.TEXT | filters.PHOTO | filters.VIDEO
    | filters.Document.ALL | filters.ANIMATION
)


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("copy_channel", copy_channel_command))
    app.add_handler(MessageHandler(
        CONTENT_FILTER & filters.ChatType.GROUPS & filters.UpdateType.MESSAGE,
        handle_group_message
    ))
    app.add_handler(MessageHandler(
        CONTENT_FILTER & filters.ChatType.GROUPS & filters.UpdateType.EDITED_MESSAGE,
        handle_edited_message
    ))

    # drop_pending_updates=False: при рестарте (а он бывает при каждом
    # деплое) накопившиеся сообщения иначе выбрасываются и никогда не
    # проверяются на спам.
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
