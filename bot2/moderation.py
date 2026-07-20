import os
import json
import aiohttp

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
