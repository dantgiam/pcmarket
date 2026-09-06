# ---------------- Команды «бро, ...» ----------------
# Сообщение, начинающееся с «бро», — не заметка, а запрос к базе. Модель
# переводит человеческую фразу в структурный фильтр, фильтр превращается в
# SQL, а результат модель оформляет обратно человеческим текстом. За счёт
# этого не нужен список команд: работает и «бро, итоги дня», и «бро, что
# там по адыгиду за неделю», и «бро, что я забросил».

import datetime as dt
import json
import re

import db
from config import (
    COMMAND_PREFIX_RE, FORGOTTEN_DAYS, PROJECTS, TZ, TYPES,
    project_title, status_title, type_title,
)
from deepseek import chat_json

_PREFIX = re.compile(COMMAND_PREFIX_RE, re.IGNORECASE)

MAX_ANSWER = 3500

INTENT_PROMPT = """Ты — маршрутизатор запросов к базе личных заметок.
Пользователь пишет фразу, ты превращаешь её в JSON-фильтр. Ничего не
придумывай и ничего не отвечай по существу — только фильтр.

Поля:
- "intent": одно из
  - "report" — сводка/итоги за период («итоги дня», «что было за неделю»)
  - "search" — поиск конкретного по смыслу («найди про пещеру»)
  - "list" — перечислить по фильтру («покажи задачи», «цитаты за август»)
  - "forgotten" — то, что давно лежит и забыто
  - "stats" — размер и состав базы
  - "help" — непонятный запрос или просьба объяснить, что бот умеет
- "project": {projects} или null
- "type": {types} или null
- "status": "new" | "kept" | "done" | "dropped" | "open" | "all" | null
  ("open" — всё незакрытое, это разумный выбор для задач)
- "days": за сколько последних дней смотреть, число или null
- "date_from", "date_to": "YYYY-MM-DD" или null, если период назван точно
- "query": строка для поиска по тексту, только для intent="search"
- "limit": сколько записей показать, число или null

Сегодня {today}.

Отвечай ТОЛЬКО валидным JSON без пояснений."""

FORMAT_PROMPT = """Ты — личный ассистент по чату заметок. Тебе дают выборку
заметок из базы и исходный запрос владельца. Оформи короткий человеческий
ответ на русском.

Правила:
- начинай сразу с сути, без приветствий и без «конечно»;
- группируй по проектам или по типу, если записей много;
- каждую заметку выводи одной строкой, начиная с её номера в формате «№12»;
- сохраняй формулировки владельца, не переписывай его мысли своими словами
  до неузнаваемости;
- если записей нет — так и скажи одной строкой, без придумывания;
- в конце, если уместно, добавь одну строку вывода или предложения;
- никакого markdown, только обычный текст и переносы строк;
- уложись в 1500 символов."""


def is_command(text):
    return bool(text) and bool(_PREFIX.match(text))


def strip_prefix(text):
    return _PREFIX.sub("", text or "", count=1).strip()


def _today():
    return dt.datetime.now(TZ).date()


def _ts(date_obj, end=False):
    """Дата → unix-время начала (или конца) суток в нашей таймзоне."""
    time_part = dt.time(23, 59, 59) if end else dt.time(0, 0, 0)
    return dt.datetime.combine(date_obj, time_part, tzinfo=TZ).timestamp()


async def _intent(request, session=None):
    system = INTENT_PROMPT.format(
        projects=" | ".join(f'"{p}"' for p in PROJECTS),
        types=" | ".join(f'"{t}"' for t in TYPES),
        today=_today().isoformat(),
    )
    result = await chat_json(system, request, session=session, max_tokens=300)
    return result if isinstance(result, dict) else None


def _resolve_period(intent):
    date_from = date_to = None

    raw_from = intent.get("date_from")
    raw_to = intent.get("date_to")
    try:
        if raw_from:
            date_from = _ts(dt.date.fromisoformat(str(raw_from)[:10]))
        if raw_to:
            date_to = _ts(dt.date.fromisoformat(str(raw_to)[:10]), end=True)
    except Exception:
        date_from = date_to = None

    if date_from is None and intent.get("days"):
        try:
            days = max(1, min(int(intent["days"]), 3650))
            date_from = _ts(_today() - dt.timedelta(days=days - 1))
        except (TypeError, ValueError):
            pass

    return date_from, date_to


def _resolve_status(value):
    if value in (None, "", "all"):
        return None
    if value == "open":
        return ["new", "kept"]
    return value


def _fetch(intent, request):
    kind = intent.get("intent") or "list"
    project = intent.get("project")
    note_type = intent.get("type")
    status = _resolve_status(intent.get("status"))
    date_from, date_to = _resolve_period(intent)

    try:
        limit = max(1, min(int(intent.get("limit") or 40), 100))
    except (TypeError, ValueError):
        limit = 40

    if kind == "search":
        query = intent.get("query") or request
        return kind, db.search_notes(query, limit=limit, project=project)

    if kind == "forgotten":
        cutoff = _ts(_today() - dt.timedelta(days=FORGOTTEN_DAYS), end=True)
        return kind, db.query_notes(
            project=project, type=note_type, status=["new"],
            date_to=cutoff, limit=limit, order="date ASC",
        )

    if kind == "report" and date_from is None:
        # «Итоги дня» без явного периода — это сегодня.
        date_from = _ts(_today())

    return kind, db.query_notes(
        project=project, type=note_type, status=status,
        date_from=date_from, date_to=date_to, limit=limit,
    )


def _rows_payload(rows):
    payload = []
    for row in rows:
        payload.append({
            "id": row["id"],
            "date": dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m.%Y"),
            "type": type_title(row["type"]),
            "project": project_title(row["project"]),
            "status": status_title(row["status"]),
            "summary": row["summary"] or " ".join((row["text"] or "").split())[:120],
            "due": row["due"],
        })
    return payload


def _plain_answer(kind, rows):
    """Запасной формат без модели — если DeepSeek недоступен, отчёт всё равно
    должен приходить: бот без интернета к модели полезнее, чем молчащий бот."""
    if not rows:
        return "Ничего не нашёл по этому запросу."

    by_project = {}
    for row in rows:
        by_project.setdefault(row["project"] or "none", []).append(row)

    lines = [f"Нашёл {len(rows)} записей:"]
    for project, items in by_project.items():
        lines.append(f"\n{project_title(project)}:")
        for row in items[:20]:
            when = dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m")
            summary = row["summary"] or " ".join((row["text"] or "").split())[:100]
            due = f" (до {row['due']})" if row["due"] else ""
            lines.append(f"  №{row['id']} [{when}] {type_title(row['type'])}: {summary}{due}")
    return "\n".join(lines)


def help_text():
    return (
        "Я разбираю этот чат заметок.\n\n"
        "Просто пиши мысли как обычно — я ставлю реакцию с типом заметки:\n"
        "🤔 мысль · 🫡 задача · ✍ цитата · 🔥 материал · 🆒 ссылка · 💯 факт\n\n"
        "Если ошибся — ответь реплаем хэштегами: «#задача #адыгид».\n"
        "Так же отмечается статус: «#сделано», «#выкинуть».\n"
        "Каждое такое исправление я запоминаю и учитываю дальше.\n\n"
        "Спросить что угодно можно через «бро»:\n"
        "  бро, итоги дня\n"
        "  бро, что по адыгиду за неделю\n"
        "  бро, какие задачи висят\n"
        "  бро, что я забросил\n"
        "  бро, найди про пещеру возле Каменномостского\n"
        "  бро, цитаты за август\n\n"
        "Команды: /stats — состав базы, /rules — мои выведенные правила."
    )


async def answer(request_text, *, session=None):
    """Возвращает (текст ответа, строки заметок) — строки нужны, чтобы
    навесить на ответ кнопки «сделано / выкинуть»."""
    request = strip_prefix(request_text)
    if not request:
        return help_text(), []

    intent = await _intent(request, session=session)

    if intent is None:
        # Модель недоступна — не отказываем, а ищем по словам запроса.
        rows = db.search_notes(request, limit=40)
        return _plain_answer("search", rows), rows

    if intent.get("intent") == "help":
        return help_text(), []

    if intent.get("intent") == "stats":
        return stats_text(), []

    kind, rows = _fetch(intent, request)

    if not rows:
        return "Ничего не нашёл по этому запросу.", []

    formatted = await chat_json(
        FORMAT_PROMPT + '\n\nВерни JSON: {"answer": "текст ответа"}',
        "Запрос владельца: " + request + "\n\nЗаметки:\n"
        + json.dumps(_rows_payload(rows), ensure_ascii=False, indent=None),
        session=session, max_tokens=1500, temperature=0.3,
    )

    text = None
    if isinstance(formatted, dict):
        text = (formatted.get("answer") or "").strip()

    if not text:
        text = _plain_answer(kind, rows)

    return text[:MAX_ANSWER], rows


def stats_text():
    data = db.stats()
    if not data["total"]:
        return "База пустая. Импортируй историю или начни писать заметки."

    lines = [f"Всего заметок: {data['total']}"]
    if data["unclassified"]:
        lines.append(f"Не разобрано: {data['unclassified']}")

    if data["first"]:
        first = dt.datetime.fromtimestamp(data["first"], TZ).strftime("%d.%m.%Y")
        last = dt.datetime.fromtimestamp(data["last"], TZ).strftime("%d.%m.%Y")
        lines.append(f"Период: {first} — {last}")

    lines.append("\nПо проектам:")
    for row in data["by_project"]:
        lines.append(f"  {project_title(row['project'])}: {row['c']}")

    lines.append("\nПо типам:")
    for row in data["by_type"]:
        lines.append(f"  {type_title(row['type'])}: {row['c']}")

    lines.append("\nПо статусу:")
    for row in data["by_status"]:
        lines.append(f"  {status_title(row['status'])}: {row['c']}")

    lines.append(f"\nМоих ошибок исправлено: {data['corrections']}")
    return "\n".join(lines)
