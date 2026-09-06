# ---------------- Дайджесты, дубликаты и вывод правил ----------------
# Модуль ничего не знает про telegram: возвращает список сообщений вида
# {"text": ..., "buttons": [[(подпись, callback_data), ...]]}, а отправляет
# их main.py. Так логику отчётов можно гонять и проверять без бота.

import datetime as dt
import json

import db
from config import (
    FORGOTTEN_DAYS, PROJECTS, TZ,
    project_title, status_title, type_title,
)
from deepseek import chat_json


def _ts(date_obj, end=False):
    time_part = dt.time(23, 59, 59) if end else dt.time(0, 0, 0)
    return dt.datetime.combine(date_obj, time_part, tzinfo=TZ).timestamp()


def _today():
    return dt.datetime.now(TZ).date()


def _short(row, limit=110):
    text = row["summary"] or " ".join((row["text"] or "").split())
    return text[:limit] if text else "(без текста)"


def task_buttons(rows, limit=5):
    buttons = []
    for row in rows[:limit]:
        buttons.append([
            (f"✔ №{row['id']}", f"done:{row['id']}"),
            (f"✖ №{row['id']}", f"drop:{row['id']}"),
        ])
    return buttons


# ---------------- Ежедневный дайджест ----------------

def build_daily():
    today = _today()
    fresh = db.query_notes(date_from=_ts(today - dt.timedelta(days=1)), limit=200)
    open_tasks = db.query_notes(type="task", status=["new", "kept"], limit=50, order="date ASC")

    if not fresh and not open_tasks:
        return []

    lines = [f"Утро, {today.strftime('%d.%m')}."]

    if fresh:
        by_project = {}
        for row in fresh:
            by_project.setdefault(row["project"] or "none", []).append(row)

        lines.append(f"\nЗа сутки прилетело {len(fresh)}:")
        for project, items in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
            kinds = {}
            for row in items:
                kinds[row["type"]] = kinds.get(row["type"], 0) + 1
            detail = ", ".join(f"{type_title(t)} — {c}" for t, c in kinds.items())
            lines.append(f"  {project_title(project)}: {detail}")
    else:
        lines.append("\nЗа сутки ничего нового.")

    if open_tasks:
        today_iso = today.isoformat()
        soon = [r for r in open_tasks if r["due"] and r["due"] <= today_iso]
        lines.append(f"\nОткрытых задач: {len(open_tasks)}")
        if soon:
            lines.append("Срок вышел или сегодня:")
            for row in soon[:5]:
                lines.append(f"  №{row['id']} {_short(row)} (до {row['due']})")

        lines.append("\nСамые давние:")
        for row in open_tasks[:5]:
            when = dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m")
            lines.append(f"  №{row['id']} [{when}] {_short(row)}")

    return [{"text": "\n".join(lines), "buttons": task_buttons(open_tasks)}]


# ---------------- Еженедельный дайджест ----------------

CONTENT_PROMPT = """Ты — редактор контента. Тебе дают накопленные заготовки
для одного проекта: обрывочные заметки, ссылки, факты, идеи.

Предложи 3 конкретных поста, которые можно собрать ИЗ ЭТИХ заготовок.
Каждый пост — одна строка: цепляющая формулировка темы, а в скобках номера
использованных заметок (например «№12, №40»). Не выдумывай материал,
которого нет в заготовках. Никакого markdown.

Верни JSON: {"ideas": ["...", "...", "..."]}"""

DEDUP_PROMPT = """Ты ищешь повторы в личных заметках. Тебе дают список
заметок одного проекта с номерами.

Найди группы, где человек несколько раз записал ПО СУТИ ОДНУ И ТУ ЖЕ мысль
разными словами. Похожая тема — это не повтор; повтор — это когда заметки
взаимозаменяемы и одна ничего не добавляет к другой. Лучше не найти ничего,
чем предложить объединить разные мысли.

Для каждой группы верни номера заметок и короткое объяснение, почему это
одно и то же.

Верни JSON: {"groups": [{"ids": [12, 40], "reason": "..."}]}
Если повторов нет — {"groups": []}"""

RULES_PROMPT = """Ты анализируешь, как владелец заметок исправляет твою
классификацию. Тебе дают список его исправлений.

Найди в них СИСТЕМУ и сформулируй не больше 3 правил, которые не дадут
повторять эти ошибки. Правило — одна короткая фраза в повелительном
наклонении, конкретная и проверяемая, например: «Заметки про водопады,
маршруты и локации Кавказа относи к adygid, даже если проект не назван».

Не формулируй правил, которые следуют из одного-единственного исправления —
это шум, а не система. Лучше вернуть пустой список.

Верни JSON: {"rules": ["...", "..."]}"""


async def _content_ideas(project, materials, session=None):
    payload = [
        {"id": r["id"], "text": _short(r, 200), "type": type_title(r["type"])}
        for r in materials
    ]
    result = await chat_json(
        CONTENT_PROMPT,
        f"Проект: {project_title(project)}\n\nЗаготовки:\n"
        + json.dumps(payload, ensure_ascii=False),
        session=session, max_tokens=800, temperature=0.5,
    )
    if not isinstance(result, dict):
        return []
    ideas = result.get("ideas") or []
    return [str(i).strip() for i in ideas if str(i).strip()][:3]


async def build_weekly(session=None):
    today = _today()
    week_start = _ts(today - dt.timedelta(days=6))
    messages = []

    week = db.query_notes(date_from=week_start, limit=500)

    lines = [f"Итоги недели ({(today - dt.timedelta(days=6)).strftime('%d.%m')} — {today.strftime('%d.%m')})"]
    if week:
        lines.append(f"\nВсего записей: {len(week)}")
        by_project = {}
        for row in week:
            by_project.setdefault(row["project"] or "none", []).append(row)
        for project, items in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {project_title(project)}: {len(items)}")
    else:
        lines.append("\nЗа неделю не было ни одной записи.")

    done = db.query_notes(status="done", date_from=week_start, limit=100)
    if done:
        lines.append(f"\nЗакрыто задач: {len(done)}")

    messages.append({"text": "\n".join(lines), "buttons": []})

    # ---- Контент-план по проектам, где накопился материал ----
    for project in PROJECTS:
        if project in ("none", "personal"):
            continue
        materials = db.query_notes(project=project, status=["new", "kept"], limit=40)
        materials = [r for r in materials if r["type"] in ("material", "link", "thought", "quote")]
        if len(materials) < 3:
            continue

        ideas = await _content_ideas(project, materials, session=session)
        if not ideas:
            continue

        text = f"{project_title(project)} — накопилось {len(materials)} заготовок.\nЧто из этого можно собрать:\n"
        text += "\n".join(f"  {i + 1}. {idea}" for i, idea in enumerate(ideas))
        messages.append({"text": text, "buttons": []})

    # ---- Забытое ----
    cutoff = _ts(today - dt.timedelta(days=FORGOTTEN_DAYS), end=True)
    forgotten = db.query_notes(status=["new"], date_to=cutoff, limit=7, order="date ASC")
    forgotten = [r for r in forgotten if r["type"] in ("thought", "task")]
    if forgotten:
        lines = [f"Лежит без движения дольше {FORGOTTEN_DAYS} дней:"]
        for row in forgotten:
            when = dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m.%y")
            lines.append(f"  №{row['id']} [{when}] {_short(row)}")
        lines.append("\nЛибо в работу, либо выкинуть — иначе они так и будут висеть.")
        messages.append({"text": "\n".join(lines), "buttons": task_buttons(forgotten, limit=5)})

    return messages


# ---------------- Предложения объединить дубликаты ----------------

async def propose_dedup(session=None, per_project=40):
    """Ничего не объединяет само — только предлагает, решение за кнопкой."""
    messages = []

    for project in PROJECTS:
        rows = db.query_notes(project=project, status=["new", "kept"], limit=per_project)
        rows = [r for r in rows if r["type"] in ("thought", "task")]
        if len(rows) < 4:
            continue

        payload = [{"id": r["id"], "text": _short(r, 200)} for r in rows]
        result = await chat_json(
            DEDUP_PROMPT,
            f"Проект: {project_title(project)}\n\nЗаметки:\n"
            + json.dumps(payload, ensure_ascii=False),
            session=session, max_tokens=900,
        )
        if not isinstance(result, dict):
            continue

        known = {r["id"] for r in rows}
        for group in (result.get("groups") or [])[:3]:
            if not isinstance(group, dict):
                continue
            try:
                ids = [int(i) for i in (group.get("ids") or [])]
            except (TypeError, ValueError):
                continue
            ids = [i for i in ids if i in known]
            if len(ids) < 2:
                continue

            reason = str(group.get("reason") or "").strip()[:300]
            group_id = db.add_dedup_group(project, ids, reason)

            notes = db.get_notes(ids)
            lines = [f"Похоже, это одно и то же ({project_title(project)}):"]
            for row in notes:
                when = dt.datetime.fromtimestamp(row["date"], TZ).strftime("%d.%m.%y")
                lines.append(f"  №{row['id']} [{when}] {_short(row, 150)}")
            if reason:
                lines.append(f"\nПочему: {reason}")
            lines.append("\nОставлю самую свежую, остальные сверну в неё. Объединяем?")

            messages.append({
                "text": "\n".join(lines),
                "buttons": [[("Объединить", f"merge:{group_id}"),
                             ("Не трогать", f"nomerge:{group_id}")]],
            })

    return messages


# ---------------- Вывод правил из исправлений ----------------

async def propose_rules(session=None):
    since = float(db.meta_get("last_rules_check", 0) or 0)
    corrections = db.corrections_since(since)

    # Меньше пяти исправлений — статистики нет, выводить правила не из чего.
    if len(corrections) < 5:
        return []

    payload = [
        {
            "text": " ".join((row["text"] or "").split())[:200],
            "было": f'{row["old_type"] or "?"}/{row["old_project"] or "?"}',
            "стало": f'{row["new_type"]}/{row["new_project"]}',
        }
        for row in corrections
    ]

    result = await chat_json(
        RULES_PROMPT,
        "Исправления владельца:\n" + json.dumps(payload, ensure_ascii=False),
        session=session, max_tokens=600,
    )

    db.meta_set("last_rules_check", dt.datetime.now(TZ).timestamp())

    if not isinstance(result, dict):
        return []

    messages = []
    for rule in (result.get("rules") or [])[:3]:
        text = str(rule).strip()[:300]
        if not text or db.rule_exists(text):
            continue
        rule_id = db.add_rule(text)
        messages.append({
            "text": "Заметил закономерность в твоих правках:\n\n"
                    f"«{text}»\n\n"
                    "Добавить это в мои постоянные правила?",
            "buttons": [[("Добавить", f"rule:{rule_id}"),
                         ("Не надо", f"norule:{rule_id}")]],
        })

    return messages
