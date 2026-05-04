import json
import sqlite3
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "agent_state.sqlite3"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session_kind ON events(session_id, kind, created_at)")


def append_event(kind: str, session_id: str, payload: dict[str, Any]) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO events(kind, session_id, payload, created_at) VALUES (?, ?, ?, ?)",
            (kind, session_id, json.dumps(payload, ensure_ascii=False), int(time.time())),
        )


def latest_events(session_id: str, kind: str, limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND kind = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, kind, max(1, min(limit, 100))),
        ).fetchall()
    result = []
    for (payload,) in rows:
        try:
            result.append(json.loads(payload))
        except Exception:
            continue
    return result
