"""SQLite persistence for saved reports and user preferences."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    username TEXT NOT NULL,
    preference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(username, preference)
);
"""


def _connect() -> sqlite3.Connection:
    db_path: Path = settings.data_dir / "app.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_report(username: str, title: str, content: str, session_id: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reports (username, title, content, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, title, content, session_id, _now()),
        )
        return cur.lastrowid


def list_reports(username: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, session_id, created_at FROM reports WHERE username = ? ORDER BY id",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_report(username: str, report_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE username = ? AND id = ?", (username, report_id)
        ).fetchone()
        return dict(row) if row else None


def delete_reports(username: str, report_ids: list[int]) -> int:
    """Delete reports by id, scoped to the owner. Returns number of rows deleted."""
    if not report_ids:
        return 0
    placeholders = ",".join("?" * len(report_ids))
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM reports WHERE username = ? AND id IN ({placeholders})",
            [username, *report_ids],
        )
        return cur.rowcount


def add_preference(username: str, preference: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO preferences (username, preference, created_at) VALUES (?, ?, ?)",
            (username, preference, _now()),
        )


def remove_preference(username: str, preference: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM preferences WHERE username = ? AND preference = ?",
            (username, preference),
        )
        return cur.rowcount


def list_preferences(username: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT preference FROM preferences WHERE username = ? ORDER BY created_at",
            (username,),
        ).fetchall()
        return [r["preference"] for r in rows]
