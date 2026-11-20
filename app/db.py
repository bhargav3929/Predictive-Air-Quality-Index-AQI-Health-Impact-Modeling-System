"""SQLite persistence: settings, sliding-window rate limiter, forecast history."""
import json
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "aeropulse.db"
RATE_LIMIT = 15
RATE_WINDOW = 3600


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _conn()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS rate_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_rate_ip_ts ON rate_hits(ip, ts);
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            city TEXT, country TEXT, lat REAL, lon REAL,
            profile TEXT, us_aqi REAL, pm2_5 REAL, pm10 REAL, no2 REAL, o3 REAL,
            payload TEXT NOT NULL
        );
        """
    )
    c.close()


# ---- settings -------------------------------------------------------------
def get_setting(key: str) -> str:
    c = _conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    if row and row["value"]:
        return row["value"]
    return os.getenv(key, "") or ""


def set_setting(key: str, value: str):
    c = _conn()
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    c.close()


def get_keys() -> dict:
    return {k: get_setting(k) for k in ("GROQ_API_KEY",)}


# ---- rate limiter ---------------------------------------------------------
def rate_hit(ip: str):
    """Atomically consume one request. Returns (allowed, remaining, reset_in_seconds)."""
    now = time.time()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM rate_hits WHERE ts < ?", (now - RATE_WINDOW,))
        rows = c.execute("SELECT ts FROM rate_hits WHERE ip=? ORDER BY ts", (ip,)).fetchall()
        if len(rows) >= RATE_LIMIT:
            c.execute("COMMIT")
            return False, 0, int(rows[0]["ts"] + RATE_WINDOW - now)
        c.execute("INSERT INTO rate_hits(ip, ts) VALUES(?,?)", (ip, now))
        c.execute("COMMIT")
        used = len(rows) + 1
        reset = int(rows[0]["ts"] + RATE_WINDOW - now) if rows else RATE_WINDOW
        return True, RATE_LIMIT - used, reset
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()


def rate_status(ip: str):
    now = time.time()
    c = _conn()
    rows = c.execute(
        "SELECT ts FROM rate_hits WHERE ip=? AND ts >= ? ORDER BY ts", (ip, now - RATE_WINDOW)
    ).fetchall()
    c.close()
    reset = int(rows[0]["ts"] + RATE_WINDOW - now) if rows else 0
    return {"limit": RATE_LIMIT, "remaining": max(0, RATE_LIMIT - len(rows)), "reset_in": reset}


def rate_refund(ip: str):
    c = _conn()
    c.execute("DELETE FROM rate_hits WHERE id=(SELECT MAX(id) FROM rate_hits WHERE ip=?)", (ip,))
    c.close()


def delete_forecast(fid: int):
    c = _conn()
    c.execute("DELETE FROM forecasts WHERE id=?", (fid,))
    c.close()


# ---- forecasts ------------------------------------------------------------
def save_forecast(result: dict) -> int:
    loc, cur = result["location"], result["current"]
    c = _conn()
    cur_ = c.execute(
        """INSERT INTO forecasts(created_at,city,country,lat,lon,profile,us_aqi,pm2_5,pm10,no2,o3,payload)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            time.time(), loc["name"], loc["country"], loc["lat"], loc["lon"], result["profile"],
            cur.get("us_aqi"), cur.get("pm2_5"), cur.get("pm10"), cur.get("no2"), cur.get("o3"),
            json.dumps(result),
        ),
    )
    fid = cur_.lastrowid
    c.close()
    return fid


def list_forecasts(limit: int = 50):
    c = _conn()
    rows = c.execute(
        "SELECT id,created_at,city,country,profile,us_aqi,pm2_5,pm10,no2 FROM forecasts ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_forecast(fid: int):
    c = _conn()
    row = c.execute("SELECT payload FROM forecasts WHERE id=?", (fid,)).fetchone()
    c.close()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["id"] = fid
    return data
