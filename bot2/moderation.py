import io
import os
import json
import aiohttp
import pytesseract
from PIL import Image

# ---------------- Антиспам-модерация через DeepSeek ----------------

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

PROMPT = """
Ты являешься системой модерации Telegram.

Тебе приходит одно сообщение пользователя.

Определи, является ли оно:

- рекламой
- коммерческим предложением
- поиском клиентов
- продажей товаров
- продажей услуг
- продвижением канала
- продвижением группы
- продвижением бота
- мошенничеством
- финансовыми схемами
- казино
- ставками
- крипто-рекламой
- массовым спамом
- просьбой написать в ЛС для сделки
- приглашением перейти куда-либо
- предложением работы, подработки, вакансией, набором сотрудников ("требуются", "в цех требуются", "ищем людей", "нужны работники", "хорошая зарплата", "пишите, обсудим")
- вербовкой с зашифрованными/странными словами вместо реального товара или занятия (подозрительные "непонятные" предложения работы или заработка)

Обычное человеческое общение НЕ является нарушением.

Даже если сообщение короткое ("ок", "ага", "спасибо", "привет", "лол") — это OK.

Это чат-каталог товаров: участники публикуют свои товары в формате
"#категория #товар цена" (например, "#аксессуары #сумка 650₽"), часто с
фото. Такие объявления от участников группы — это НЕ нарушение, это
нормальная витрина товаров чата, всегда OK, даже если есть хэштеги, цена
и слово "продажа"/"распродажа".

Примеры нарушений (BAN):
- "Добрый день, в цех требуются сборщики капризных макарон, пишите, обсудим"
- "Ищу людей на удалёнку, доход от 5000 в день, пиши в лс"
- "Набираем сотрудников, гибкий график, детали в личке"

Отвечай ТОЛЬКО JSON.

Если нужно забанить:

{"action":"BAN"}

Если сообщение нормальное:

{"action":"OK"}

Никакого текста кроме JSON.
"""


def ocr_image(image_bytes: bytes) -> str:
    """Распознаёт текст на картинке (рекламные объявления часто шлют
    картинкой без подписи — весь текст "зашит" в саму картинку, поэтому
    обычная проверка по тексту/подписи их пропускает). При любой ошибке —
    пустая строка (не блокируем модерацию)."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(image, lang="rus+eng").strip()
    except Exception:
        return ""


async def check_spam(text: str) -> str:
    """Возвращает "BAN" или "OK". При любой ошибке — "OK" (не баним)."""
    if not DEEPSEEK_API_KEY:
        return "OK"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "temperature": 0,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text},
        ],
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(DEEPSEEK_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    return "OK"

                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()

                try:
                    result = json.loads(content)
                    return result.get("action", "OK")
                except Exception:
                    return "OK"
    except Exception:
        return "OK"


async def confirm_intent(text: str, question: str) -> bool:
    """Уточняющий вопрос к DeepSeek для защиты от ложных срабатываний
    быстрого фаззи-фильтра автоответчика (например, "швабра не работает"
    текстово похоже на "вы работаете?", но по смыслу — не про график).
    При отсутствии ключа/ошибке — доверяем быстрому фильтру (True)."""
    if not DEEPSEEK_API_KEY:
        return True

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Ответь только одним словом: 'да' или 'нет'. Вопрос: {question} "
                    "Отвечай по смыслу сообщения целиком, а не по случайному "
                    "текстовому сходству отдельных слов с вопросом."
                ),
            },
            {"role": "user", "content": text},
        ],
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(DEEPSEEK_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"⚠️ confirm_intent: DeepSeek вернул статус {resp.status}: {body[:300]}")
                    return True

                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                print(f"🔎 confirm_intent: вопрос={question!r}, текст={text!r}, ответ DeepSeek={content!r}")
                return content.lower().startswith("да")
    except Exception as e:
        print(f"⚠️ confirm_intent: исключение {e}")
        return True
