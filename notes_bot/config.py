# ---------------- Настройки личного бота-ассистента по чату заметок ----------------
# Отдельный сервис: свой токен, своя база, свой Railway-проект. С ботами
# магазинов (bot2/bot3) не пересекается ничем, кроме ключа DeepSeek.

import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

# Консоль Windows по умолчанию в cp1251: любой print с эмодзи или частью
# юникода валит скрипт с UnicodeEncodeError. Импорт истории запускается
# как раз с локальной машины, поэтому переводим вывод в UTF-8 сразу.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---- Доступы ----

BOT_TOKEN = os.environ.get("NOTES_BOT_TOKEN")

# Владелец: сообщения от всех остальных игнорируются молча. Без этого любой,
# кто найдёт бота, получит доступ к личным заметкам и к оплаченному DeepSeek.
OWNER_ID = int(os.environ.get("NOTES_OWNER_ID") or 0)

# Чат заметок. Если не задан — бот на первое же сообщение напечатает в лог
# id чата, чтобы его можно было прописать в переменные окружения.
NOTES_CHAT_ID = int(os.environ.get("NOTES_CHAT_ID") or 0)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ---- База ----

# На Railway файловая система контейнера эфемерная: без примонтированного
# тома база с историей заметок сотрётся при первом же редеплое. Поэтому путь
# выносится в переменную окружения и указывается на точку монтирования тома
# (например, /data/notes.db).
DB_PATH = os.environ.get("NOTES_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "notes.db"
)

# ---- Расписание ----

def _timezone(name):
    """На Windows системной базы таймзон нет, и без пакета tzdata ZoneInfo
    падает прямо на импорте конфига. Лучше отработать в UTC с честным
    предупреждением, чем не запуститься вообще."""
    try:
        return ZoneInfo(name)
    except Exception as e:
        print(f"⚠️ Таймзона {name} недоступна ({type(e).__name__}) — работаю в UTC. "
              f"Поставь пакет tzdata: pip install tzdata")
        return dt.timezone.utc


TZ = _timezone(os.environ.get("NOTES_TZ", "Europe/Moscow"))
DAILY_HOUR = int(os.environ.get("NOTES_DAILY_HOUR", "9"))
WEEKLY_HOUR = int(os.environ.get("NOTES_WEEKLY_HOUR", "20"))
WEEKLY_WEEKDAY = int(os.environ.get("NOTES_WEEKLY_WEEKDAY", "6"))  # 0 = пн, 6 = вс

# Выключатели на случай, если дайджесты начнут раздражать.
DAILY_ENABLED = os.environ.get("NOTES_DAILY_ENABLED", "1") != "0"
WEEKLY_ENABLED = os.environ.get("NOTES_WEEKLY_ENABLED", "1") != "0"

# ---- Самоочистка чата ----
# Сообщения самого бота (отчёты, ответы на «бро», предложения) живут
# ограниченное время и удаляются, если на них не поставлено сердечко.
# Так чат заметок не зарастает служебной перепиской, а всё нужное
# сохраняется одним нажатием.

AUTODELETE_ENABLED = os.environ.get("NOTES_AUTODELETE_ENABLED", "1") != "0"
AUTODELETE_MINUTES = int(os.environ.get("NOTES_AUTODELETE_MINUTES", "30"))

# Реакция, которая оставляет сообщение навсегда. Telegram присылает сердечко
# как U+2764 без «эмодзи-довеска» U+FE0F, поэтому при сравнении его срезаем.
KEEP_REACTION = "❤"


def plain_emoji(value):
    """Срезает вариационный селектор U+FE0F: одно и то же сердечко приходит
    то как «❤», то как «❤️», и без нормализации сравнение врёт."""
    return (value or "").replace("️", "")

# ---- Таксономия ----

TYPES = {
    "thought": "мысль",
    "task": "задача",
    "quote": "цитата",
    "material": "материал",
    "link": "ссылка",
    "fact": "факт",
}

TYPE_DESCRIPTIONS = {
    "thought": "мысль, идея, размышление, гипотеза, «хорошо бы сделать»",
    "task": "конкретное дело, которое нужно выполнить; часто с дедлайном или в повелительном наклонении",
    "quote": "чужая цитата, афоризм, выписка из книги, видео, разговора",
    "material": "заготовка для контента: фото, видео, скриншот, история, факт, который пойдёт в пост",
    "link": "сообщение, вся суть которого — ссылка на внешний ресурс",
    "fact": "справочная информация «просто помнить»: адрес, номер, цена, координаты, режим работы",
}

PROJECTS = {
    "adygid": "Адыгид",
    "memes": "Мемы",
    "teens": "Подростки",
    "personal": "Личное",
    "none": "Без проекта",
}

PROJECT_DESCRIPTIONS = {
    "adygid": "туристический проект про Адыгею: сайт, телеграм-канал, тикток. "
              "Маршруты, водопады, горы, локации, цены для туристов, Кавказ",
    "memes": "аккаунт с мемами в тиктоке: шутки, тренды, форматы, референсы",
    "teens": "проект для подростков о взрослой жизни: тикток и телеграм. "
             "Деньги, работа, документы, самостоятельность, отношения — объяснённые подростку",
    "personal": "личное, не для публикации: свои дела, здоровье, деньги, учёба, быт, отношения",
    "none": "не относится ни к одному из проектов",
}

STATUSES = {
    "new": "новое",
    "kept": "в работе",
    "done": "сделано",
    "dropped": "выкинуто",
}

# ---- Реакции ----
# ВАЖНО: Telegram разрешает ботам ставить только эмодзи из своего фиксированного
# набора реакций. Привычные 💡📌✅🎬 в него НЕ входят — запрос с ними падает с
# REACTION_INVALID. Ниже — только валидные. Менять можно на любые из набора:
# 👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🎉 🤩 🙏 👌 🕊 😍 💯 🤣 ⚡ 🏆 🤨 🍓 😈 🤓 👻 👨‍💻 👀
# 🙈 😇 🤝 ✍ 🤗 🫡 💅 🤪 🗿 🆒 💘 🦄 😎 👾 🤷

REACTION_PROCESSING = "👀"   # принял, думаю
REACTION_UNKNOWN = "🤷"      # не смог классифицировать
REACTION_DONE = "🏆"         # отмечено сделанным

TYPE_REACTIONS = {
    "thought": "🤔",
    "task": "🫡",
    "quote": "✍",
    "material": "🔥",
    "link": "🆒",
    "fact": "💯",
}

# ---- Алиасы для ручных исправлений хэштегами ----
# Пишешь реплаем «#задача #адыгид» — бот перезаписывает классификацию
# и запоминает исправление как обучающий пример.

TYPE_ALIASES = {
    "мысль": "thought", "мысли": "thought", "идея": "thought", "идеи": "thought",
    "мыслишка": "thought", "thought": "thought", "idea": "thought",
    "задача": "task", "задачи": "task", "дело": "task", "туду": "task",
    "todo": "task", "task": "task",
    "цитата": "quote", "цитаты": "quote", "quote": "quote",
    "материал": "material", "материалы": "material", "контент": "material",
    "material": "material", "content": "material",
    "ссылка": "link", "ссылки": "link", "линк": "link", "link": "link",
    "факт": "fact", "факты": "fact", "инфа": "fact", "справка": "fact", "fact": "fact",
}

PROJECT_ALIASES = {
    "адыгид": "adygid", "адыгея": "adygid", "туризм": "adygid", "тур": "adygid",
    "adygid": "adygid", "adygea": "adygid",
    "мем": "memes", "мемы": "memes", "memes": "memes", "meme": "memes",
    "подростки": "teens", "подросток": "teens", "тинс": "teens", "тины": "teens",
    "teens": "teens", "teen": "teens",
    "личное": "personal", "себе": "personal", "личка": "personal",
    "personal": "personal", "me": "personal",
    "безпроекта": "none", "нет": "none", "none": "none", "общее": "none",
}

STATUS_ALIASES = {
    "сделано": "done", "готово": "done", "выполнено": "done", "done": "done",
    "выкинуть": "dropped", "удалить": "dropped", "мусор": "dropped",
    "неактуально": "dropped", "drop": "dropped",
    "вработе": "kept", "оставить": "kept", "делаю": "kept", "keep": "kept",
    "новое": "new", "new": "new",
}

# ---- Прочее ----

# Сообщение, начинающееся с этого слова, — не заметка, а команда боту.
COMMAND_PREFIX_RE = r"^\s*бро\b[\s,!;:.\-—]*"

# Сколько собственных исправлений подмешивать в промпт классификатора.
FEWSHOT_LIMIT = 15

# Потолок на выведенные правила: без него промпт распухнет и правила начнут
# противоречить друг другу.
RULES_LIMIT = 30

# Идея считается «забытой», если столько дней лежит нетронутой.
FORGOTTEN_DAYS = int(os.environ.get("NOTES_FORGOTTEN_DAYS", "30"))


def type_title(slug):
    return TYPES.get(slug, "не разобрано")


def project_title(slug):
    return PROJECTS.get(slug, "без проекта")


def status_title(slug):
    return STATUSES.get(slug, slug or "?")


def type_reaction(slug):
    return TYPE_REACTIONS.get(slug, REACTION_UNKNOWN)
