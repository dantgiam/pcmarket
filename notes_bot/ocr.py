# ---------------- Распознавание текста на картинках ----------------
# Упрощённая версия того, что уже работает в bot2/moderation.py. В чате
# заметок картинки — это в основном скриншоты переписок, статей и заметок,
# то есть обычный тёмный текст на светлом фоне. Поэтому агрессивная
# бинаризация с инверсией здесь не нужна, хватает контраста и апскейла.

import io

try:
    import pytesseract
    from PIL import Image, ImageOps
    _AVAILABLE = True
except Exception as e:  # pragma: no cover - зависит от окружения
    print(f"⚠️ OCR: библиотеки недоступны ({type(e).__name__}: {e}) — картинки будут без текста")
    _AVAILABLE = False

_warned = False


def available() -> bool:
    """Есть ли вообще рабочий Tesseract. Проверяется один раз, лениво."""
    global _warned
    if not _AVAILABLE:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception as e:
        if not _warned:
            print(f"⚠️ OCR: Tesseract не найден ({type(e).__name__}: {e}) — картинки будут без текста")
            _warned = True
        return False


def image_to_text(image_bytes: bytes) -> str:
    """Текст с картинки. При любой ошибке — пустая строка: заметка всё равно
    должна сохраниться, просто без распознанного текста."""
    if not image_bytes or not available():
        return ""

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("L")
        image = ImageOps.autocontrast(image)
        # Мелкий скриншот Tesseract читает заметно хуже — растягиваем.
        if image.width < 1200:
            scale = 1200 / image.width
            image = image.resize((int(image.width * scale), int(image.height * scale)))
    except Exception as e:
        print(f"⚠️ OCR: не удалось открыть картинку ({type(e).__name__}: {e})")
        return ""

    chunks = []
    seen = set()
    # psm 3 — связный текст страницы, psm 6 — единый блок (типичный скриншот).
    for psm in (3, 6):
        try:
            chunk = pytesseract.image_to_string(image, lang="rus+eng", config=f"--psm {psm}").strip()
        except Exception as e:
            print(f"⚠️ OCR: ошибка распознавания psm={psm} ({type(e).__name__}: {e})")
            continue
        norm = " ".join(chunk.split()).lower()
        if norm and norm not in seen:
            seen.add(norm)
            chunks.append(chunk)

    text = "\n".join(chunks).strip()
    # Совсем короткий результат — это, как правило, мусор из шума картинки.
    return text if len(text) >= 8 else ""


def file_to_text(path: str) -> str:
    """То же самое, но для файла на диске — нужно импорту истории, когда
    экспорт выгружен вместе с фотографиями."""
    try:
        with open(path, "rb") as f:
            return image_to_text(f.read())
    except Exception as e:
        print(f"⚠️ OCR: не удалось прочитать {path} ({type(e).__name__}: {e})")
        return ""
