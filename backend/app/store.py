"""SQLite: request log, response cache, and daily usage counters.
shortcut: SQLite on local disk. Upgrade path: Postgres via SQLAlchemy with accounts in P4 (SPEC §6).
"""
import json
import sqlite3
import time
from datetime import date

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY, ts REAL, user TEXT, endpoint TEXT, provider TEXT, model TEXT,
  cache_key TEXT, request TEXT, response TEXT, valid INTEGER, error TEXT, retries INTEGER,
  cached INTEGER, latency_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, created REAL);
-- What students do around a check (the friends test, PROJECT.md §11): one row per event, `data` as JSON.
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, user TEXT, check_id TEXT, event TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS usage (
  user TEXT, day TEXT, cost_class TEXT, count INTEGER, PRIMARY KEY (user, day, cost_class)
);
"""


def connect() -> sqlite3.Connection:
    # A connection per call: FastAPI runs sync handlers on a thread pool.
    c = sqlite3.connect(config.DB_PATH, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def log(**row) -> None:
    row.setdefault("ts", time.time())
    for k in ("request", "response"):
        if k in row and not isinstance(row[k], str):
            row[k] = json.dumps(row[k], default=str)
    cols = ",".join(row)
    with connect() as c:
        c.execute(f"INSERT INTO request_log ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def event(user: str, check_id: str, event: str, data: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO events (ts, user, check_id, event, data) VALUES (?, ?, ?, ?, ?)",
                  (time.time(), user, check_id, event, json.dumps(data)))


def cache_get(key: str):
    with connect() as c:
        r = c.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else None


def cache_put(key: str, value) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(value), time.time()))


def take_quota(user: str, cost_class: str) -> bool:
    """Atomically count one call; False if the user's daily cap for this class is used up."""
    limit = config.CAPS[cost_class]
    with connect() as c:
        cur = c.execute(
            """INSERT INTO usage VALUES (?, ?, ?, 1)
               ON CONFLICT (user, day, cost_class) DO UPDATE SET count = count + 1 WHERE count < ?""",
            (user, date.today().isoformat(), cost_class, limit),
        )
        return cur.rowcount == 1
