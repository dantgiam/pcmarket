import os
import sys

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from auto_reply import handle_auto_reply
from moderation import check_spam

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot3"))
import max_client
import relay_state
from relay_shops import RELAY_SHOPS

# Обратная карта: tg_chat_id -> max_chat_id, для релея TG -> MAX
MAX_CHAT_MAP = {shop["tg_chat_id"]: max_chat_id for max_chat_id, shop in RELAY_SHOPS.items()}

TOKEN = os.environ.get("TOKENOTVET")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")

# Чат, куда падают логи банов (с кнопкой «Разбанить»). ID через env.
_log = os.environ.get("LOG_CHAT_ID")
LOG_CHAT_ID = int(_log) if _log else None

# Пользователи, которых нельзя банить и кто может разбанивать
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
    user = update.effective_user
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


async def send_ban_log(context, chat, user, text: str):
    """Отправляет в чат-логов запись о бане с кнопкой разбана."""
    if not LOG_CHAT_ID:
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "♻️ Разбанить",
            callback_data=f"unban:{chat.id}:{user.id}"
        )
    ]])

    log_text = (
        "🚫 Забанен спамер\n\n"
        f"👤 {user_label(user)}\n"
        f"💬 Чат: {chat.title or chat.id}\n\n"
        f"📝 Сообщение:\n{text}"
    )

    try:
        await context.bot.send_message(
            LOG_CHAT_ID, log_text, reply_markup=keyboard
        )
    except Exception:
        pass


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет сообщение на спам через DeepSeek и банит нарушителя.
    Возвращает True, если сообщение расценено как спам (значит, его не нужно
    релеить в MAX, даже если сам бан по какой-то причине не удался)."""
    text = update.message.text or update.message.caption
    if not text:
        return False

    action = await check_spam(text)
    if action != "BAN":
        return False

    user = update.effective_user
    chat = update.effective_chat

    try:
        await update.message.delete()
    except Exception:
        pass

    banned = False
    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        banned = True
    except Exception:
        pass

    if banned:
        await send_ban_log(context, chat, user, text)

    return True


async def unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки «Разбанить» в чате-логов."""
    query = update.callback_query

    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Разбанить может только администратор", show_alert=True)
        return

    try:
        _, chat_id, user_id = query.data.split(":")
        await context.bot.unban_chat_member(
            chat_id=int(chat_id),
            user_id=int(user_id),
            only_if_banned=True,
        )
    except Exception:
        await query.answer("Не удалось разбанить", show_alert=True)
        return

    await query.answer("Разбанен")
    try:
        await query.edit_message_text(
            f"{query.message.text}\n\n✅ Разбанен ({query.from_user.full_name})"
        )
    except Exception:
        pass


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ID текущего чата — чтобы узнать LOG_CHAT_ID."""
    await update.message.reply_text(f"🆔 Chat ID: {update.effective_chat.id}")


async def relay_to_max(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin: bool) -> None:
    """Пересылает сообщение из TG-группы в связанный MAX-чат (bot3/relay_shops.py).
    Админов не подписываем, остальных — "TG, Имя: текст"."""
    if not MAX_BOT_TOKEN:
        return

    max_chat_id = MAX_CHAT_MAP.get(update.effective_chat.id)
    if not max_chat_id:
        return

    text = update.message.text or update.message.caption or ""
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

    # Обычный вопрос клиента (адрес / график / MAX) — отвечаем и не баним
    handled_by_auto_reply = await handle_auto_reply(update, context)

    # Админов и доверенных не модерируем, их сообщения в MAX идут без подписи
    exempt = await is_exempt(update, context)

    # Вопрос про адрес/график уже отвечен на месте — дальше (в MAX) не пересылаем
    if handled_by_auto_reply:
        return

    is_spam = False
    if not exempt:
        is_spam = await moderate(update, context)

    if not is_spam:
        await relay_to_max(update, context, is_admin=exempt)


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CallbackQueryHandler(unban_callback, pattern=r"^unban:"))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO)
        & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP),
        handle_group_message
    ))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
