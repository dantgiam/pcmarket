import os
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

from auto_reply import handle_auto_reply
from moderation import check_spam

TOKEN = os.environ.get("TOKENOTVET")

# Пользователи, которых нельзя банить (владельцы / доверенные)
ADMIN_IDS = [1014380197, 866973179]


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


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет сообщение на спам через DeepSeek и банит нарушителя."""
    text = update.message.text or update.message.caption
    if not text:
        return

    action = await check_spam(text)
    if action != "BAN":
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
        )
    except Exception:
        pass


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обычный вопрос клиента (адрес / график / MAX) — отвечаем и не баним
    if await handle_auto_reply(update, context):
        return

    # Админов и доверенных не модерируем
    if await is_exempt(update, context):
        return

    await moderate(update, context)


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO)
        & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP),
        handle_group_message
    ))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
