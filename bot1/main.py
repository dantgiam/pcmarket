from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import os


# -------------------- Настройки --------------------
TOKEN = os.environ.get("TOKEN")

# ссылки на чаты магазиновF
SHOP_1 = "https://t.me/PolCenimarketMaykop"  # Основная
SHOP_2 = "https://t.me/polcenimarketmaikop1" # Черема
SHOP_3 = "https://t.me/polcenimarketmaikop2" # Батарейная
SHOP_4 = "https://t.me/polcenimarketlabinsk" # Лабинск



# стартовое сообщение
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Майкоп, ул. Строителей 8Б", url=SHOP_1)],
        [InlineKeyboardButton("📍 Майкоп, ул. Депутатская 16Б", url=SHOP_2)],
        [InlineKeyboardButton("📍 Майкоп, ул. Батарейная", url=SHOP_3)],
        [InlineKeyboardButton("📍 Лабинск, ул. Победы 161", url=SHOP_4)],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Благодаря группе вы можете следить за актуальными поступлениями и оставлять свои отзывы! Скоро появится возможность предоплаты (брони) товара!\n\n"
        "Выберите адрес который ближе к вам, и откроете чат нужного магазина:",
        reply_markup=reply_markup
    )


# ответ на любые сообщения
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Задать все вопросы можно в чате, я просто бот-помощник 😄"
    )


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.ALL, handle_message))

app.run_polling()