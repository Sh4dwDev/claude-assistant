"""Tiny SQLite layer. One file (data/assistant.db) you can open with any SQLite viewer."""
import json
import sqlite3
import threading
import time
from typing import Any

from .config import DB_FILE

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY, title TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,           -- user | assistant
  content_json TEXT NOT NULL,   -- exact API content blocks (replayed to the API unchanged)
  display_text TEXT,            -- what the dashboard shows; NULL = hidden (tool results)
  created REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT 'general', text TEXT NOT NULL,
  created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY, title TEXT, body TEXT, url TEXT, level TEXT, created REAL, read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY, ts REAL, month TEXT, service TEXT, detail TEXT, cost_usd REAL, meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_month ON usage_log(month, service);
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id INTEGER PRIMARY KEY, endpoint TEXT UNIQUE, data TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY, name TEXT, os TEXT, role TEXT DEFAULT 'pc', mac TEXT, broadcast TEXT,
  token_hash TEXT UNIQUE, created REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, agent_id INTEGER, title TEXT, project TEXT, prompt TEXT,
  status TEXT,            -- queued | waking | running | done | failed | cancelled
  session_id TEXT, summary TEXT, last_log TEXT, cost_usd REAL DEFAULT 0,
  created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS job_logs (
  id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE, ts REAL, text TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_logs ON job_logs(job_id, id);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)
    conn().commit()


def q(sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql: str, args: tuple = ()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql: str, args: tuple = ()) -> int:
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


# --- settings (non-secret preferences) -------------------------------------
DEFAULTS: dict[str, Any] = {
    "assistant_name": "Claude",
    "user_name": "",  # asked on the setup page, stored only in your database
    "home": "",
    "timezone": "Europe/Oslo",
    "chat_model": "claude-opus-5-5",
    "chat_effort": "low",
    "server_cost_eur": 4.5,
    "eur_usd": 1.08,
    "cap_claude_api_usd": 20.0,
    "cap_phone_usd": 5.0,
    "cap_tts_usd": 5.0,
    "speak_replies": False,
    "pc_idle_sleep_min": 0,  # 0 = never put the PC to sleep automatically
}


def get_setting(key: str) -> Any:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else DEFAULTS.get(key)


def set_setting(key: str, value: Any) -> None:
    run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)))


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULTS)
    for r in q("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out


def now() -> float:
    return time.time()
