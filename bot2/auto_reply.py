import re
import string
from rapidfuzz import fuzz
from telegram import Update
from telegram.ext import ContextTypes
from shops import SHOPS
from moderation import confirm_intent

CONFIRM_QUESTIONS = {
    "address": "Спрашивает ли пользователь адрес/местоположение именно ЭТОГО магазина (того, где идёт переписка), а не другого магазина сети?",
    "work_time": "Спрашивает ли пользователь про график/часы/время работы магазина?",
    "max_link": "Спрашивает ли пользователь, есть ли этот магазин в мессенджере MAX?",
    "other_shop": "Спрашивает ли пользователь про другой магазин сети, а не про текущий?",
    "tg_link": "Просит ли пользователь ссылку на Telegram (тг) этого магазина, или спрашивает, есть ли у них Telegram/тг?",
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

OTHER_SHOP_KEYWORDS = [
    "другой магазин", "другие магазины", "другая точка", "другие точки",
    "ещё магазины", "еще магазины", "все магазины", "остальные магазины",
    "магазины в других городах", "в другом городе", "список магазинов",
    "какие есть магазины", "ваши магазины", "другой чат", "другую группу",
]

TG_LINK_KEYWORDS = [
    "ссылка на телеграм", "ссылка на тг", "есть ли телеграм",
    "есть ли вы в телеграм", "есть ли вы в тг", "группа в телеграме",
    "чат в телеграме", "телеграм канал", "тг канал", "тг чат",
]

OTHER_SHOP_TEXT = (
    "🖥 Вот наш сайт, тут ссылки на все наши магазины, "
    "можете выбрать ближайший👌\nhttps://polcenimarket.ru/"
)

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


async def handle_auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Отвечает на вопрос про адрес / график / MAX.
    Возвращает True, если ответил (значит сообщение — обычный вопрос клиента)."""
    if not update.message:
        return False

    text = update.message.text or update.message.caption
    if not text:
        return False
    chat_id = update.effective_chat.id

    if is_blacklisted_link(text):
        return False

    shop = SHOPS.get(chat_id)
    if not shop:
        return False

    if is_relevant(text, OTHER_SHOP_KEYWORDS) and await confirm_intent(text, CONFIRM_QUESTIONS["other_shop"]):
        await update.message.reply_text(
            OTHER_SHOP_TEXT,
            disable_web_page_preview=True,
            reply_to_message_id=update.message.message_id
        )
        return True

    if is_relevant(text, ADDRESS_KEYWORDS) and await confirm_intent(text, CONFIRM_QUESTIONS["address"]):
        await update.message.reply_text(
            shop["address"],
            reply_to_message_id=update.message.message_id
        )
        return True

    if is_relevant(text, WORK_KEYWORDS) and await confirm_intent(text, CONFIRM_QUESTIONS["work_time"]):
        await update.message.reply_text(
            shop["work_time"],
            reply_to_message_id=update.message.message_id
        )
        return True

    if is_relevant(text, MAX_KEYWORDS) and await confirm_intent(text, CONFIRM_QUESTIONS["max_link"]):
        await update.message.reply_text(
            shop["max_link"],
            disable_web_page_preview=True,
            parse_mode="Markdown",
            reply_to_message_id=update.message.message_id
        )
        return True

    return False