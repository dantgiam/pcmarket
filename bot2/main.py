import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from auto_reply import handle_auto_reply
from moderation import check_spam

TOKEN = os.environ.get("TOKENOTVET")

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


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет сообщение на спам через DeepSeek и банит нарушителя."""
    text = update.message.text or update.message.caption
    if not text:
        return

    action = await check_spam(text)
    if action != "BAN":
        return

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


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # В самом чате-логов ничего не модерируем
    if LOG_CHAT_ID and update.effective_chat.id == LOG_CHAT_ID:
        return

    # Обычный вопрос клиента (адрес / график / MAX) — отвечаем и не баним
    if await handle_auto_reply(update, context):
        return

    # Админов и доверенных не модерируем
    if await is_exempt(update, context):
        return

    await moderate(update, context)


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
