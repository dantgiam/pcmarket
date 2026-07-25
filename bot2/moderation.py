import asyncio
import io
import os
import json
import aiohttp
import pytesseract
from PIL import Image, ImageOps

# ---------------- Антиспам-модерация через DeepSeek ----------------

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Тестовый тег: если он есть в тексте/подписи сообщения админа, модерация
# обрабатывает это сообщение как от обычного участника — чтобы можно было
# проверить OCR/бан/удаление, тестируя со своего же админского аккаунта.
TEST_NON_ADMIN_TAG = "#т3"

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

Текст может быть распознан с картинки (OCR) — тогда он бывает рваным, с
опечатками и обрывками слов. Суди по смыслу, а не по грамотности.

Скриншот чужого маркетплейса (Wildberries, Ozon, Avito, Юла, AliExpress,
Яндекс Маркет), который участник прислал обсудить товар — это НЕ нарушение,
это OK. Признаки такого скриншота: элементы интерфейса приложения
("в корзину", "добавить в корзину", рейтинг, "отзывов", цена со скидкой).
Но само по себе упоминание маркетплейса НЕ делает сообщение безопасным:
если это рекламная афиша услуг (оформление документов, ремонт, кредиты,
работа, эвакуатор, установка техники) с призывом связаться или номером
телефона — это нарушение, даже если рядом написано "Ozon" или "в корзину".

Примеры нарушений (BAN):
- рекламный баннер услуг с телефоном и призывом "обращайтесь в личные сообщения"
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


def _otsu_threshold(hist: list) -> int:
    """Порог Otsu по гистограмме (256 бинов) — без numpy/cv2."""
    total = sum(hist)
    if total == 0:
        return 127
    sum_total = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    w_b = 0
    max_var = -1.0
    threshold = 127
    for i in range(256):
        w_b += hist[i]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += i * hist[i]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = i
    return threshold


def _ocr_variants(base):
    """Готовит несколько версий картинки для OCR: серую, чёрно-белую
    (бинаризация Otsu) и её инверсию — стилизованный светлый текст на
    тёмном фоне обычный OCR часто не видит, а на ч/б после инверсии
    (тёмный текст на светлом) читается заметно лучше."""
    variants = [base]
    t = _otsu_threshold(base.histogram())
    bw = base.point(lambda p: 255 if p > t else 0)
    variants.append(bw)
    variants.append(ImageOps.invert(bw))
    return variants


def ocr_image(image_bytes: bytes) -> str:
    """Распознаёт текст на картинке (рекламные объявления часто рисуют
    весь текст прямо на картинке, стилизованным шрифтом на градиентном
    фоне — обычный OCR без предобработки такое нередко не видит).
    При любой ошибке — пустая строка (не блокируем модерацию)."""
    if not image_bytes:
        return ""

    # Явная проверка, что бинарь Tesseract вообще доступен — иначе
    # pytesseract кидает TesseractNotFoundError, которое иначе молча
    # проглатывается и выглядит как "текст не распознан".
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:
        print(f"⚠️ OCR: Tesseract НЕ доступен ({type(e).__name__}: {e})")
        return ""

    try:
        base = Image.open(io.BytesIO(image_bytes)).convert("L")
        base = ImageOps.autocontrast(base)
        if base.width < 1200:
            scale = 1200 / base.width
            base = base.resize((int(base.width * scale), int(base.height * scale)))
    except Exception as e:
        print(f"⚠️ OCR: не удалось открыть картинку ({type(e).__name__}: {e})")
        return ""

    seen = set()
    texts = []
    # Несколько вариантов картинки × режимов сегментации: psm 3 —
    # обычный связный текст, psm 11 — разрозненные надписи/плашки,
    # типичные для рекламных картинок.
    for variant in _ocr_variants(base):
        for psm in (3, 11):
            try:
                chunk = pytesseract.image_to_string(variant, lang="rus+eng", config=f"--psm {psm}").strip()
            except Exception as e:
                print(f"⚠️ OCR: ошибка распознавания psm={psm} ({type(e).__name__}: {e})")
                continue
            norm = " ".join(chunk.split()).lower()
            if norm and norm not in seen:
                seen.add(norm)
                texts.append(chunk)
    return "\n".join(texts)


CHECK_SPAM_ATTEMPTS = 3


async def check_spam(text: str) -> str:
    """Возвращает "BAN" или "OK". При ошибке — "OK" (не баним), но ошибку
    обязательно логируем: молчаливый пропуск означает, что весь спам этого
    периода прошёл незамеченным, и об этом нужно знать."""
    if not DEEPSEEK_API_KEY:
        print("⚠️ check_spam: DEEPSEEK_API_KEY не задан — модерация не работает, всё пропускается!")
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

    # Таймауты и 429/5xx у DeepSeek — обычное дело под нагрузкой, поэтому
    # повторяем: без ретраев каждый такой сбой = пропущенный спам.
    for attempt in range(1, CHECK_SPAM_ATTEMPTS + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(DEEPSEEK_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        print(f"⚠️ check_spam: DeepSeek статус {resp.status} (попытка {attempt}): {body}")
                        if resp.status < 500 and resp.status != 429:
                            return "OK"  # ошибка запроса, повтор не поможет
                        await asyncio.sleep(attempt)
                        continue

                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()

                    try:
                        return json.loads(content).get("action", "OK")
                    except Exception:
                        print(f"⚠️ check_spam: ответ DeepSeek не JSON: {content[:200]}")
                        return "OK"
        except Exception as e:
            print(f"⚠️ check_spam: ошибка запроса (попытка {attempt}): {type(e).__name__}: {e}")
            await asyncio.sleep(attempt)

    print("⚠️ check_spam: все попытки исчерпаны — сообщение пропущено без проверки")
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
                    "Вопрос, просьба, требование и команда считаются одинаково — "
                    "например, 'дайте адрес', 'скиньте ссылку', 'где вы находитесь' "
                    "и 'какой у вас адрес' по смыслу равнозначны. Ориентируйся на "
                    "то, чего хочет пользователь по смыслу, а не на форму фразы."
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
                return content.lower().startswith("да")
    except Exception as e:
        print(f"⚠️ confirm_intent: исключение {e}")
        return True
