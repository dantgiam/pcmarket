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
- "ids": список номеров конкретных заметок, если владелец на них ссылается
  (например «а где сама ссылка» после ответа, где была заметка №2 → [2]).
  Номера бери из предыдущего ответа ассистента, если он приложен.
- "action": "drop" | "done" | "keep" | null — заполняй ТОЛЬКО если владелец
  прямо просит что-то сделать с заметками: «удали», «выкинь» → "drop";
  «сделано», «готово» → "done"; «верни в работу» → "keep". Во всех
  остальных случаях null.

Если приложен предыдущий ответ ассистента, новый запрос почти всегда
относится к нему: уточнение, просьба показать подробности или действие
над упомянутыми там заметками.

Сегодня {today}.

Отвечай ТОЛЬКО валидным JSON без пояснений."""

FORMAT_PROMPT = """Ты — личный ассистент по чату заметок. Тебе дают выборку
заметок из базы и исходный запрос владельца. Оформи короткий человеческий
ответ на русском.

Правила:
- начинай сразу с сути, без приветствий и без «конечно»;
- группируй по проектам или по типу, если записей много;
- каждую заметку выводи одной строкой, начиная с её номера в формате «№12»;
- ССЫЛКИ ПРИВОДИ ЦЕЛИКОМ. Если у заметки заполнено поле "links" — вставь
  адрес полностью, как есть. Никогда не заменяй ссылку описанием вроде
  «ссылка на статью» и не обрезай её: владельцу нужен сам адрес, чтобы
  по нему перейти;
- сохраняй формулировки владельца, не переписывай его мысли своими словами
  до неузнаваемости; если он просит подробности — бери их из поля "text",
  а не из короткого "summary";
- если записей нет — так и скажи одной строкой, без придумывания;
- в конце, если уместно, добавь одну строку вывода или предложения;
- никакого markdown, только обычный текст и переносы строк;
- уложись в 1500 символов.

Если приложен предыдущий ответ ассистента — владелец продолжает тот же
разговор, и отвечать надо именно на его уточнение, а не начинать заново."""


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


async def _intent(request, context_text=None, session=None):
    system = INTENT_PROMPT.format(
        projects=" | ".join(f'"{p}"' for p in PROJECTS),
        types=" | ".join(f'"{t}"' for t in TYPES),
        today=_today().isoformat(),
    )
    result = await chat_json(system, _with_context(request, context_text),
                             session=session, max_tokens=300)
    return result if isinstance(result, dict) else None


def _with_context(request, context_text):
    """Реплай на сообщение бота — продолжение разговора. Без предыдущего
    ответа фраза «а где сама ссылка» не значит ничего, и раньше она
    улетала в общий поиск и возвращала справку."""
    if not context_text:
        return request
    return (f"Предыдущий ответ ассистента:\n{context_text[:2000]}\n\n"
            f"Новый запрос владельца:\n{request}")


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


def _wanted_ids(intent):
    raw = intent.get("ids")
    if not isinstance(raw, (list, tuple)):
        return []
    ids = []
    for value in raw[:20]:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _fetch(intent, request):
    # Владелец сослался на конкретные номера («а где сама ссылка» про №2) —
    # тогда никакие фильтры не нужны, берём именно эти заметки.
    ids = _wanted_ids(intent)
    if ids:
        return intent.get("intent") or "list", db.get_notes(ids)

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


LINK_RE = re.compile(r"https?://\S+")


def links_of(row):
    """Ссылки из исходного текста заметки. Раньше в модель уходил только
    пересказ, и адрес терялся: бот отвечал «ссылка на статью о смотровых
    площадках», а самой ссылки в ответе не было."""
    return LINK_RE.findall(row["text"] or "")[:5]


def _rows_payload(rows):
    payload = []
    for row in rows:
        raw = " ".join((row["text"] or "").split())
        item = {
            "id": row["id"],
            "date": dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m.%Y"),
            "type": type_title(row["type"]),
            "project": project_title(row["project"]),
            "status": status_title(row["status"]),
            "summary": row["summary"] or raw[:120],
            "text": raw[:400],
            "due": row["due"],
        }
        links = links_of(row)
        if links:
            item["links"] = links
        payload.append(item)
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
            due = f" (до {row['due']})" if row["due"] else ""
            lines.append(f"  №{row['id']} [{when}] {type_title(row['type'])}: {_short(row)}{due}")
            for link in links_of(row):
                lines.append(f"    {link}")
    return "\n".join(lines)


def _short(row, limit=100):
    return row["summary"] or " ".join((row["text"] or "").split())[:limit] or "(без текста)"


# Действия над заметками. Статусы обратимы — ничего не удаляется навсегда.
ACTIONS = {"drop": "dropped", "done": "done", "keep": "kept"}
ACTION_TITLES = {"dropped": "выкинул", "done": "отметил сделанным", "kept": "вернул в работу"}


def _apply_action(action, rows):
    status = ACTIONS[action]

    # Массовая правка по расплывчатому запросу — верный способ потерять
    # нужное, поэтому просим уточнить вместо того, чтобы менять всё подряд.
    if len(rows) > 10:
        return (f"Под этот запрос попадает {len(rows)} заметок — слишком много, "
                "чтобы менять их разом. Назови номера или сузь запрос.", rows, None)

    ids = []
    for row in rows:
        db.set_status(row["id"], status)
        ids.append(row["id"])

    listed = "\n".join(f"  №{row['id']} {_short(row)}" for row in rows)
    return (f"Готово, {ACTION_TITLES[status]}:\n{listed}", [], {"status": status, "ids": ids})


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
        "Можно просто ответить реплаем на мой ответ — я продолжу тот же\n"
        "разговор: «а где сама ссылка», «покажи подробнее», «удали эту запись».\n\n"
        "Мои сообщения удаляются через полчаса, чтобы не засорять чат.\n"
        "Поставь сердечко — и сообщение останется навсегда.\n\n"
        "Команды: /stats — состав базы, /rules — мои выведенные правила."
    )


async def answer(request_text, *, context_text=None, session=None):
    """Возвращает (текст ответа, строки для кнопок, результат действия).

    context_text — предыдущий ответ бота, если владелец ответил на него
    реплаем: без этого продолжение разговора не понять.
    Третий элемент не None, когда запрос менял статусы: по нему main.py
    обновляет реакции на самих заметках."""
    request = strip_prefix(request_text)
    if not request:
        return help_text(), [], None

    intent = await _intent(request, context_text=context_text, session=session)

    if intent is None:
        # Модель недоступна — не отказываем, а ищем по словам запроса.
        rows = db.search_notes(request, limit=40)
        return _plain_answer("search", rows), rows, None

    ids = _wanted_ids(intent)

    if intent.get("intent") == "help" and not ids:
        return help_text(), [], None

    if intent.get("intent") == "stats":
        return stats_text(), [], None

    kind, rows = _fetch(intent, request)

    action = intent.get("action")
    if action in ACTIONS:
        if not rows:
            return "Не понял, к каким заметкам это относится.", [], None
        return _apply_action(action, rows)

    if not rows:
        return "Ничего не нашёл по этому запросу.", [], None

    user = "Запрос владельца: " + request
    if context_text:
        user = f"Предыдущий ответ ассистента:\n{context_text[:2000]}\n\n" + user
    user += "\n\nЗаметки:\n" + json.dumps(_rows_payload(rows), ensure_ascii=False, indent=None)

    formatted = await chat_json(
        FORMAT_PROMPT + '\n\nВерни JSON: {"answer": "текст ответа"}',
        user, session=session, max_tokens=1500, temperature=0.3,
    )

    text = None
    if isinstance(formatted, dict):
        text = (formatted.get("answer") or "").strip()

    if not text:
        text = _plain_answer(kind, rows)

    return text[:MAX_ANSWER], rows, None


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
