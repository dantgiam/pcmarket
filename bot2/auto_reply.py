import re
import string
from rapidfuzz import fuzz
from telegram import Update
from telegram.ext import ContextTypes

# ---------------- Магазины ----------------
SHOPS = {
    -1003450185997: {
        "address": "📍 Наш адрес: Майкоп, ул. Строителей 8Б (район железного рынка)",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/IMHKjeOxfKJFcRQTQVrhlCGvLx-qOzAUiTpxCussSr0) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/"
    },
    -1003777692701: {
        "address": "📍 Наш адрес: Майкоп, ул. Депутатская 16Б",
        "work_time": "🕒 Мы работаем: 10:00–20:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/WZ8T-qgVdTK7He20c2UAvDcawKYbedKxKFmKVZbWovo) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/"
    },
    -1003974367383: {
        "address": "📍 Наш адрес: Тульский, ул. Октябрьская 24в",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/Z_wDekAQYLNppAF0gLx00V2JfaK6S7ljY_pjp-RZH_I) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/"
    },
    -1003840431977: {
        "address": "📍 Наш адрес: Лабинск, ул. Победы 161",
        "work_time": "🕒 Мы работаем: 09:00–18:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/caMNU_JQa9Q1-UlwqS1r6G9AECURkQn0ARdLGtM25wI) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/"
    },
    -1003973787679: {
        "address": "📍 Наш адрес: Усть-Лабинск, ул. Октябрьская 105",
        "work_time": "🕒 Мы работаем: 10:00–20:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/0QISzsN7l3pOozTObbzM4OR9YEmvVjaUtuXq7i-Jolo) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/"
    }
}

BLACKLIST = ["есть"]
BLACKLIST_LINKS = ["https://max.ru/join"]

ADDRESS_KEYWORDS = [
    "адрес", "где найти", "где приехать",
    "где вы", "где находитесь", "как найти"
]

WORK_KEYWORDS = [
    "время работы", "работаете",
    "до скольки", "рабочий день",
    "график работы"
]

MAX_KEYWORDS = [
    "max", "макс", "в максе",
    "есть ли макс", "есть ли вы в максе",
    "ссылка на макс", "есть ли max",
    "есть ли вы в max", "соцсеть макс"
]

THRESHOLD = 85


def clean(text: str) -> str:
    return text.lower().translate(
        str.maketrans('', '', string.punctuation)
    ).strip()


def is_blacklisted_link(text: str) -> bool:
    return any(link in text for link in BLACKLIST_LINKS)


def is_relevant(text: str, keywords: list) -> bool:
    text = clean(text)

    if text in BLACKLIST:
        return False

    if "сколько" in text and "работ" not in text:
        return False

    for word in keywords:
        clean_word = clean(word)

        if re.search(r'\b' + re.escape(clean_word) + r'\b', text):
            return True

        if fuzz.partial_ratio(clean_word, text) >= THRESHOLD:
            return True

    return False


async def handle_auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text or update.message.caption
    if not text:
        return
    chat_id = update.effective_chat.id

    if is_blacklisted_link(text):
        return

    shop = SHOPS.get(chat_id)
    if not shop:
        return

    if is_relevant(text, ADDRESS_KEYWORDS):
        await update.message.reply_text(
            shop["address"],
            reply_to_message_id=update.message.message_id
        )
        return

    if is_relevant(text, WORK_KEYWORDS):
        await update.message.reply_text(
            shop["work_time"],
            reply_to_message_id=update.message.message_id
        )
        return

    if is_relevant(text, MAX_KEYWORDS):
        await update.message.reply_text(
            shop["max_link"],
            disable_web_page_preview=True,
            parse_mode="Markdown",
            reply_to_message_id=update.message.message_id
        )
        return