# ---------------- Хранилище заметок ----------------
# Ключевая идея всей затеи: каждое сообщение проходит через модель ОДИН раз
# и превращается в строку этой таблицы. Дальше любые отчёты и выборки — это
# SQL, а не новый запрос к DeepSeek. Поэтому отчёты мгновенные, бесплатные и
# воспроизводимые, а история не переклассифицируется при каждом вопросе.

import json
import os
import re
import sqlite3
import time

from config import DB_PATH, FEWSHOT_LIMIT, RULES_LIMIT

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date REAL NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    ocr_text TEXT,
    media TEXT,
    file_id TEXT,
    needs_transcription INTEGER NOT NULL DEFAULT 0,
    type TEXT,
    project TEXT,
    tags TEXT,
    summary TEXT,
    due TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    corrected INTEGER NOT NULL DEFAULT 0,
    merged_into INTEGER,
    source TEXT NOT NULL DEFAULT 'live',
    created_at REAL NOT NULL,
    UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);
CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project, status);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type, status);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    text, summary, tags,
    content='notes', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, text, summary, tags)
    VALUES (new.id, new.text, new.summary, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text, summary, tags)
    VALUES ('delete', old.id, old.text, old.summary, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text, summary, tags)
    VALUES ('delete', old.id, old.text, old.summary, old.tags);
    INSERT INTO notes_fts(rowid, text, summary, tags)
    VALUES (new.id, new.text, new.summary, new.tags);
END;

-- Ручные исправления классификации. Это и есть обучающая выборка: свежие
-- и релевантные примеры подмешиваются в промпт классификатора.
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER,
    text TEXT NOT NULL,
    old_type TEXT,
    old_project TEXT,
    new_type TEXT,
    new_project TEXT,
    created_at REAL NOT NULL
);

-- Правила, выведенные из исправлений и подтверждённые владельцем кнопкой.
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at REAL NOT NULL,
    decided_at REAL
);

-- Предложения объединить дубликаты. Ничего не делается без подтверждения.
CREATE TABLE IF NOT EXISTS dedup_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    note_ids TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at REAL NOT NULL
);

-- Сообщения самого бота. Живут ограниченное время: если владелец не пометил
-- сообщение сердечком, оно удаляется, и чат заметок остаётся чистым.
CREATE TABLE IF NOT EXISTS bot_messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sent_at REAL NOT NULL,
    keep INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_bot_messages_sweep ON bot_messages(keep, sent_at);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect():
    directory = os.path.dirname(os.path.abspath(DB_PATH))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ---------------- Заметки ----------------

def add_note(*, chat_id, message_id, date, text="", ocr_text=None, media=None,
             file_id=None, needs_transcription=0, source="live"):
    """Возвращает (note_id, is_new). Повторная вставка того же сообщения
    невозможна — на этом держится совместимость импорта истории и живого
    потока: если сообщение уже разобрано, второй раз оно в модель не пойдёт."""
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO notes
               (chat_id, message_id, date, text, ocr_text, media, file_id,
                needs_transcription, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, message_id, date, text or "", ocr_text, media, file_id,
             int(needs_transcription), source, now),
        )
        if cur.rowcount:
            return cur.lastrowid, True

        row = conn.execute(
            "SELECT id FROM notes WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return (row["id"] if row else None), False


def get_note(note_id):
    with _connect() as conn:
        return conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()


def get_note_by_message(chat_id, message_id):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()


def get_notes(note_ids):
    if not note_ids:
        return []
    placeholders = ",".join("?" * len(note_ids))
    with _connect() as conn:
        return conn.execute(
            f"SELECT * FROM notes WHERE id IN ({placeholders}) ORDER BY date",
            tuple(note_ids),
        ).fetchall()


def update_classification(note_id, *, type=None, project=None, tags=None,
                          summary=None, due=None):
    with _connect() as conn:
        conn.execute(
            """UPDATE notes SET type = ?, project = ?, tags = ?, summary = ?, due = ?
               WHERE id = ?""",
            (type, project, json.dumps(tags or [], ensure_ascii=False), summary, due, note_id),
        )


def update_text(note_id, text, ocr_text=None):
    """Сообщение отредактировали — обновляем текст, чтобы переклассифицировать."""
    with _connect() as conn:
        conn.execute(
            "UPDATE notes SET text = ?, ocr_text = COALESCE(?, ocr_text) WHERE id = ?",
            (text, ocr_text, note_id),
        )


def apply_correction(note_id, *, new_type=None, new_project=None):
    """Ручное исправление классификации. Возвращает True, если что-то изменилось."""
    note = get_note(note_id)
    if note is None:
        return False

    target_type = new_type or note["type"]
    target_project = new_project or note["project"]
    if target_type == note["type"] and target_project == note["project"]:
        return False

    now = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE notes SET type = ?, project = ?, corrected = 1 WHERE id = ?",
            (target_type, target_project, note_id),
        )
        conn.execute(
            """INSERT INTO corrections
               (note_id, text, old_type, old_project, new_type, new_project, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (note_id, _note_text(note), note["type"], note["project"],
             target_type, target_project, now),
        )
    return True


def set_status(note_id, status):
    with _connect() as conn:
        conn.execute("UPDATE notes SET status = ? WHERE id = ?", (status, note_id))


def _note_text(note):
    parts = [note["text"] or ""]
    if note["ocr_text"]:
        parts.append(f"[с картинки] {note['ocr_text']}")
    return "\n".join(p for p in parts if p).strip()


# ---------------- Выборки ----------------

def query_notes(*, project=None, type=None, status=None, date_from=None,
                date_to=None, has_due=False, limit=50, order="date DESC"):
    where = ["merged_into IS NULL"]
    params = []

    if project and project != "all":
        where.append("project = ?")
        params.append(project)
    if type and type != "all":
        where.append("type = ?")
        params.append(type)
    if status and status != "all":
        if isinstance(status, (list, tuple, set)):
            where.append(f"status IN ({','.join('?' * len(status))})")
            params.extend(status)
        else:
            where.append("status = ?")
            params.append(status)
    if date_from is not None:
        where.append("date >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("date <= ?")
        params.append(date_to)
    if has_due:
        where.append("due IS NOT NULL")

    if order not in ("date DESC", "date ASC", "due ASC"):
        order = "date DESC"

    sql = f"SELECT * FROM notes WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
    params.append(int(limit))

    with _connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def _fts_tokens(query):
    """Токены для FTS5. У unicode61 нет русской морфологии, поэтому длинные
    слова обрезаются до основы и ищутся как префикс: «пещеру» → «пещер»*,
    что находит и «пещера», и «пещеры». Грубо, но работает без словарей."""
    raw = re.findall(r"\w+", (query or "").lower(), flags=re.UNICODE)
    tokens = []
    for word in raw:
        if len(word) < 3:
            continue
        stem = word[: max(4, len(word) - 2)] if len(word) > 5 else word
        tokens.append(f'"{stem}"*')
        if len(tokens) >= 8:
            break
    return tokens


def search_notes(query, *, limit=25, project=None):
    """Поиск по тексту. Сначала строгий (все слова), при пустом результате —
    мягкий (любое из слов)."""
    tokens = _fts_tokens(query)
    if not tokens:
        return []

    extra = ""
    params_tail = []
    if project and project != "all":
        extra = " AND n.project = ?"
        params_tail.append(project)

    # Без алиаса у виртуальной таблицы: MATCH и bm25() обращаются к ней по
    # имени, с алиасом SQLite ругается на «no such column: notes_fts».
    sql = f"""
        SELECT n.* FROM notes_fts
        JOIN notes n ON n.id = notes_fts.rowid
        WHERE notes_fts MATCH ? AND n.merged_into IS NULL{extra}
        ORDER BY bm25(notes_fts) LIMIT ?
    """

    with _connect() as conn:
        for joiner in (" AND ", " OR "):
            match = joiner.join(tokens)
            try:
                rows = conn.execute(sql, (match, *params_tail, int(limit))).fetchall()
            except sqlite3.OperationalError as e:
                print(f"⚠️ Поиск: некорректный запрос FTS ({e})")
                return []
            if rows:
                return rows
    return []


def unclassified(limit=200, chat_id=None):
    """Заметки, до которых классификатор ещё не дошёл. Это и есть чекпоинт
    импорта: упал на середине — перезапустил, и он продолжит с этого места,
    ничего не переклассифицируя заново."""
    sql = "SELECT * FROM notes WHERE type IS NULL"
    params = []
    if chat_id is not None:
        sql += " AND chat_id = ?"
        params.append(chat_id)
    sql += " ORDER BY date LIMIT ?"
    params.append(int(limit))

    with _connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def count_unclassified(chat_id=None):
    sql = "SELECT COUNT(*) c FROM notes WHERE type IS NULL"
    params = []
    if chat_id is not None:
        sql += " AND chat_id = ?"
        params.append(chat_id)
    with _connect() as conn:
        return conn.execute(sql, tuple(params)).fetchone()["c"]


def stats():
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
        unclassified = conn.execute(
            "SELECT COUNT(*) c FROM notes WHERE type IS NULL"
        ).fetchone()["c"]
        by_type = conn.execute(
            "SELECT type, COUNT(*) c FROM notes WHERE merged_into IS NULL GROUP BY type ORDER BY c DESC"
        ).fetchall()
        by_project = conn.execute(
            "SELECT project, COUNT(*) c FROM notes WHERE merged_into IS NULL GROUP BY project ORDER BY c DESC"
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, COUNT(*) c FROM notes WHERE merged_into IS NULL GROUP BY status ORDER BY c DESC"
        ).fetchall()
        span = conn.execute("SELECT MIN(date) a, MAX(date) b FROM notes").fetchone()
        corrections = conn.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]

    return {
        "total": total,
        "unclassified": unclassified,
        "by_type": by_type,
        "by_project": by_project,
        "by_status": by_status,
        "first": span["a"],
        "last": span["b"],
        "corrections": corrections,
    }


# ---------------- Исправления и правила ----------------

def recent_corrections(limit=300):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM corrections ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def corrections_since(ts, limit=200):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM corrections WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (ts, limit),
        ).fetchall()


def _words(text):
    return {w for w in re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE) if len(w) > 3}


def similar_corrections(text, limit=FEWSHOT_LIMIT):
    """Примеры для few-shot: сначала самые похожие на входящее сообщение по
    пересечению слов, добиваем самыми свежими. Отдельный FTS-индекс ради
    этого не нужен — исправлений будет сотни, а не миллионы."""
    rows = recent_corrections(300)
    if not rows:
        return []

    target = _words(text)
    scored = []
    for row in rows:
        overlap = len(target & _words(row["text"])) if target else 0
        scored.append((overlap, row["created_at"], row))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[:limit]]


def active_rules():
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM rules WHERE status = 'active' ORDER BY created_at LIMIT ?",
            (RULES_LIMIT,),
        ).fetchall()


def add_rule(text, status="proposed"):
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO rules (text, status, created_at) VALUES (?, ?, ?)",
            (text, status, now),
        )
        return cur.lastrowid


def set_rule_status(rule_id, status):
    with _connect() as conn:
        conn.execute(
            "UPDATE rules SET status = ?, decided_at = ? WHERE id = ?",
            (status, time.time(), rule_id),
        )


def get_rule(rule_id):
    with _connect() as conn:
        return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()


def all_rules():
    with _connect() as conn:
        return conn.execute("SELECT * FROM rules ORDER BY status, created_at").fetchall()


def rule_exists(text):
    """Сравнение делаем в Python: встроенный lower() у SQLite работает только
    с латиницей, поэтому «Заметки» и «заметки» он считает разными строками —
    и повторные правила на русском не отсекались бы вообще."""
    target = " ".join((text or "").split()).casefold()
    if not target:
        return False

    with _connect() as conn:
        rows = conn.execute("SELECT text FROM rules").fetchall()

    return any(" ".join((row["text"] or "").split()).casefold() == target for row in rows)


# ---------------- Дубликаты ----------------

def add_dedup_group(project, note_ids, reason):
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO dedup_groups (project, note_ids, reason, created_at)
               VALUES (?, ?, ?, ?)""",
            (project, json.dumps(note_ids), reason, time.time()),
        )
        return cur.lastrowid


def get_dedup_group(group_id):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM dedup_groups WHERE id = ?", (group_id,)
        ).fetchone()


def set_dedup_status(group_id, status):
    with _connect() as conn:
        conn.execute("UPDATE dedup_groups SET status = ? WHERE id = ?", (status, group_id))


def merge_notes(keep_id, drop_ids):
    """Объединение — не удаление: заметки остаются в базе, помечаются как
    свёрнутые в keep_id и просто перестают попадать в выборки."""
    if not drop_ids:
        return
    with _connect() as conn:
        for note_id in drop_ids:
            conn.execute(
                "UPDATE notes SET merged_into = ?, status = 'dropped' WHERE id = ?",
                (keep_id, note_id),
            )


# ---------------- Сообщения бота (самоочистка чата) ----------------

def track_bot_message(chat_id, message_id):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_messages (chat_id, message_id, sent_at, keep) "
            "VALUES (?, ?, ?, 0)",
            (chat_id, message_id, time.time()),
        )


def set_bot_message_keep(chat_id, message_id, keep):
    """True, если это действительно сообщение бота и отметка изменилась.
    Реакции на обычные заметки сюда не попадают — там просто нет строки."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE bot_messages SET keep = ? WHERE chat_id = ? AND message_id = ?",
            (1 if keep else 0, chat_id, message_id),
        )
        return cur.rowcount > 0


def expired_bot_messages(before_ts, limit=50):
    with _connect() as conn:
        return conn.execute(
            "SELECT chat_id, message_id FROM bot_messages "
            "WHERE keep = 0 AND sent_at < ? ORDER BY sent_at LIMIT ?",
            (before_ts, int(limit)),
        ).fetchall()


def forget_bot_message(chat_id, message_id):
    with _connect() as conn:
        conn.execute(
            "DELETE FROM bot_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )


# ---------------- Служебное ----------------

def meta_get(key, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key, value):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def note_text(note):
    return _note_text(note)


def note_tags(note):
    try:
        return json.loads(note["tags"] or "[]")
    except Exception:
        return []
