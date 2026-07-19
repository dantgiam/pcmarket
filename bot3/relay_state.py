# ---------------- Общий журнал соответствий MAX <-> Telegram ----------------
# Нужен для reply-threading: когда админ отвечает в MAX на пересланное из TG
# сообщение (и наоборот), бот находит исходное сообщение и отвечает именно на
# него. bot2 и bot3 — разные процессы, поэтому используем общий файл SQLite
# (лежит рядом с репозиторием, в .gitignore) вместо памяти процесса.

import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "relay_state.db")
RETENTION_SECONDS = 30 * 24 * 60 * 60  # 30 дней


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_map (
            max_mid TEXT,
            max_chat_id INTEGER,
            tg_message_id INTEGER,
            tg_chat_id INTEGER,
            created_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_max_mid ON relay_map(max_mid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_msg ON relay_map(tg_chat_id, tg_message_id)")
    return conn


def save_mapping(max_mid: str, max_chat_id: int, tg_message_id: int, tg_chat_id: int) -> None:
    if not max_mid or not tg_message_id:
        return

    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO relay_map (max_mid, max_chat_id, tg_message_id, tg_chat_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (max_mid, max_chat_id, tg_message_id, tg_chat_id, now),
        )
        conn.execute("DELETE FROM relay_map WHERE created_at < ?", (now - RETENTION_SECONDS,))


def find_tg_by_max_mid(max_mid: str):
    """Возвращает (tg_chat_id, tg_message_id) или None."""
    if not max_mid:
        return None

    with _connect() as conn:
        row = conn.execute(
            "SELECT tg_chat_id, tg_message_id FROM relay_map WHERE max_mid = ? ORDER BY created_at DESC LIMIT 1",
            (max_mid,),
        ).fetchone()

    return tuple(row) if row else None


def find_max_by_tg_message(tg_chat_id: int, tg_message_id: int):
    """Возвращает (max_chat_id, max_mid) или None."""
    if not tg_message_id:
        return None

    with _connect() as conn:
        row = conn.execute(
            "SELECT max_chat_id, max_mid FROM relay_map WHERE tg_chat_id = ? AND tg_message_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (tg_chat_id, tg_message_id),
        ).fetchone()

    return tuple(row) if row else None
