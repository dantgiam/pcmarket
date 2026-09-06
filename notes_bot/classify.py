# ---------------- Классификация заметок ----------------
# «Самообучение» тут — это управляемый контекст, а не дообучение модели:
#   1) каждое ручное исправление ложится в таблицу corrections;
#   2) в промпт подмешиваются самые релевантные исправления как примеры;
#   3) из накопленных исправлений раз в неделю выводятся правила, и те,
#      что владелец подтвердил кнопкой, становятся постоянной частью промпта.
# Эффект даёт в основном пункт 2 — и стоит он почти ничего.

import datetime as dt

import db
from config import (
    PROJECT_ALIASES, PROJECT_DESCRIPTIONS, PROJECTS,
    TYPE_ALIASES, TYPE_DESCRIPTIONS, TYPES,
)
from deepseek import chat_json

MAX_TEXT = 3000  # длинные простыни режем: сути это не меняет, а токены экономит


def _taxonomy_block():
    types = "\n".join(f'- "{slug}" — {TYPE_DESCRIPTIONS[slug]}' for slug in TYPES)
    projects = "\n".join(f'- "{slug}" — {PROJECT_DESCRIPTIONS[slug]}' for slug in PROJECTS)
    return types, projects


def _rules_block():
    rules = db.active_rules()
    if not rules:
        return "(пока нет)"
    return "\n".join(f"- {row['text']}" for row in rules)


def _fewshot_block(text):
    rows = db.similar_corrections(text)
    if not rows:
        return "(пока нет)"

    lines = []
    for row in rows:
        sample = " ".join((row["text"] or "").split())[:200]
        if not sample:
            continue
        was = f'{row["old_type"] or "?"}/{row["old_project"] or "?"}'
        now = f'{row["new_type"]}/{row["new_project"]}'
        lines.append(f'- «{sample}» → {now} (модель предлагала {was} — это было неверно)')
    return "\n".join(lines) if lines else "(пока нет)"


def build_system_prompt(sample_text=""):
    types, projects = _taxonomy_block()
    return f"""Ты разбираешь личный чат заметок одного человека. В этот чат он
годами скидывает свои мысли, дела, цитаты и заготовки для своих проектов.
Твоя работа — по каждому сообщению определить, что это и к чему относится.

ТИП ЗАМЕТКИ (поле "type"), ровно одно значение:
{types}

ПРОЕКТ (поле "project"), ровно одно значение:
{projects}

Также верни:
- "tags": от 1 до 4 коротких тегов строчными буквами, без решётки, по-русски
- "summary": одна строка до 100 символов — суть заметки своими словами
- "due": срок в формате YYYY-MM-DD, если он есть в тексте явно или косвенно
  («до пятницы», «завтра»); если срока нет — null

ЛИЧНЫЕ ПРАВИЛА ВЛАДЕЛЬЦА (они важнее твоих общих соображений):
{_rules_block()}

ЕГО СОБСТВЕННЫЕ ИСПРАВЛЕНИЯ ТВОЕЙ РАБОТЫ (главный ориентир — он всегда прав):
{_fewshot_block(sample_text)}

Учитывай:
- заметки часто обрывочные, без знаков препинания, из одного слова — это нормально,
  всё равно выбери самый вероятный тип и проект;
- текст «[с картинки]» получен распознаванием скриншота, он бывает рваным;
- если проект явно не назван, ориентируйся на тему: водопад и маршрут — это adygid,
  шутка или тренд — memes, объяснение взрослой жизни — teens, своё бытовое — personal;
- не выдумывай срок, если его нет.

Отвечай ТОЛЬКО валидным JSON, без пояснений и без markdown-разметки."""


def _normalize_slug(value, aliases, allowed, default):
    if not value:
        return default
    key = str(value).strip().lower().lstrip("#")
    if key in allowed:
        return key
    if key in aliases:
        return aliases[key]
    return default


def normalize(result):
    """Приводит ответ модели к нашим слагам. Модель периодически отвечает
    по-русски («задача» вместо "task») — это не ошибка, это надо переварить."""
    if not isinstance(result, dict):
        return None

    tags = result.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace("#", "").split(",")]
    tags = [str(t).strip().lower().lstrip("#") for t in tags if str(t).strip()][:4]

    summary = (result.get("summary") or "").strip()[:200] or None

    due = result.get("due")
    if due:
        due = str(due).strip()[:10]
        try:
            dt.date.fromisoformat(due)
        except Exception:
            due = None
    else:
        due = None

    return {
        "type": _normalize_slug(result.get("type"), TYPE_ALIASES, TYPES, "thought"),
        "project": _normalize_slug(result.get("project"), PROJECT_ALIASES, PROJECTS, "none"),
        "tags": tags,
        "summary": summary,
        "due": due,
    }


def _format_message(text, date=None, extra=None):
    when = ""
    if date:
        when = f"Дата сообщения: {date}\n"
    context = f"Контекст: {extra}\n" if extra else ""
    body = (text or "").strip()[:MAX_TEXT] or "(пустое сообщение, только вложение)"
    return f"{when}{context}Сообщение:\n{body}"


async def classify(text, *, date=None, extra=None, session=None):
    """Разбор одного сообщения. None — если модель не ответила: заметка при
    этом всё равно сохранится, просто без типа, и её можно переклассифицировать."""
    system = build_system_prompt(text)
    user = _format_message(text, date, extra) + """

Верни JSON вида:
{"type": "...", "project": "...", "tags": ["..."], "summary": "...", "due": null}"""

    result = await chat_json(system, user, session=session, max_tokens=400)
    return normalize(result)


async def classify_batch(items, *, session=None):
    """Пачка сообщений за один запрос — так импортируется история.
    items: [{"i": 0, "date": "2024-05-01", "text": "..."}]
    Возвращает {i: {...}} только для тех, что модель разобрала."""
    if not items:
        return {}

    joined_text = " ".join((item.get("text") or "")[:200] for item in items)
    system = build_system_prompt(joined_text)

    lines = []
    for item in items:
        body = " ".join((item.get("text") or "").split())[:600] or "(пустое сообщение, только вложение)"
        lines.append(f'### {item["i"]} | {item.get("date", "")}\n{body}')

    user = (
        "Ниже несколько сообщений из чата заметок. Каждое начинается со строки\n"
        "«### номер | дата». Разбери КАЖДОЕ независимо от остальных.\n\n"
        + "\n\n".join(lines)
        + "\n\nВерни JSON-массив, по одному объекту на сообщение, в том же порядке:\n"
          '[{"i": 0, "type": "...", "project": "...", "tags": ["..."], "summary": "...", "due": null}]'
    )

    result = await chat_json(system, user, session=session, max_tokens=4000)

    if isinstance(result, dict):
        # Модель иногда оборачивает массив в объект — достаём первый список.
        for value in result.values():
            if isinstance(value, list):
                result = value
                break

    if not isinstance(result, list):
        return {}

    out = {}
    for entry in result:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        normalized = normalize(entry)
        if normalized:
            out[index] = normalized
    return out


def parse_correction(text):
    """«#задача #адыгид #сделано» → (type, project, status). Любой элемент
    может быть None. Возвращает None, если это вообще не исправление."""
    from config import STATUS_ALIASES

    tokens = [t for t in (text or "").split() if t.startswith("#")]
    if not tokens:
        return None

    new_type = new_project = new_status = None
    matched = False

    for token in tokens:
        key = token[1:].strip().lower().replace("ё", "е")
        if key in TYPE_ALIASES:
            new_type = TYPE_ALIASES[key]
            matched = True
        elif key in PROJECT_ALIASES:
            new_project = PROJECT_ALIASES[key]
            matched = True
        elif key in STATUS_ALIASES:
            new_status = STATUS_ALIASES[key]
            matched = True

    if not matched:
        return None
    return new_type, new_project, new_status
