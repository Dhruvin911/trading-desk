"""Shared config, state, clock and journal helpers for the trading desk.

Used by the `desk` CLI (on/off switch and cycle runner) and by the order guard
hook, so the hook and the runner always agree on what "on" and "in the window"
mean.
"""
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("DESK_ROOT") or Path(__file__).resolve().parent)
DESK = ROOT / "desk"
CONFIG_PATH = DESK / "config.json"
STATE_PATH = DESK / "state.json"
JOURNAL_DIR = DESK / "journal"

RTH_OPEN = dt.time(9, 30)
RTH_CLOSE = dt.time(16, 0)
PREMARKET_OPEN = dt.time(4, 0)

DEFAULT_STATE = {"enabled": False, "mode": "paper", "enabled_until": None, "killed": False}


def load_config(path=CONFIG_PATH):
    return json.loads(Path(path).read_text())


def save_config(cfg, path=CONFIG_PATH):
    _write_json(Path(path), cfg)


def load_state(path=STATE_PATH):
    try:
        saved = json.loads(Path(path).read_text())
    except FileNotFoundError:
        saved = {}
    return {**DEFAULT_STATE, **saved}


def save_state(state, path=STATE_PATH):
    _write_json(Path(path), state)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


# ---- clock -----------------------------------------------------------------

def tz(cfg):
    return ZoneInfo(cfg.get("timezone", "America/New_York"))


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def local_now(cfg, now=None):
    return (now or utcnow()).astimezone(tz(cfg))


def parse_hm(text):
    hour, minute = text.split(":")
    return dt.time(int(hour), int(minute))


def is_enabled(state, now=None):
    if not state.get("enabled") or state.get("killed"):
        return False
    until = state.get("enabled_until")
    return until is None or (now or utcnow()) < dt.datetime.fromisoformat(until)


def in_entry_window(cfg, now=None):
    local = local_now(cfg, now)
    if local.weekday() >= 5:
        return False
    t = local.time()
    return any(parse_hm(w["start"]) <= t < parse_hm(w["end"]) for w in cfg["entry_windows"])


def phase(cfg, state, now=None):
    """What the desk should do right now.

    closed   - weekend or outside 9:30-16:00 ET (holidays are not detected;
               Robinhood rejects orders and data is flat, so cycles do nothing)
    off      - market open but the desk is switched off / killed / expired
    entries  - inside an entry window: manage positions and look for setups
    manage   - between windows: manage open positions only, no new entries
    flatten  - after flatten_at: close intraday positions, no new entries
    """
    local = local_now(cfg, now)
    t = local.time()
    if local.weekday() >= 5 or not (RTH_OPEN <= t < RTH_CLOSE):
        return "closed"
    if not is_enabled(state, now):
        return "off"
    if t >= parse_hm(cfg["flatten_at"]):
        return "flatten"
    if in_entry_window(cfg, now):
        return "entries"
    return "manage"


def session_times(cfg, now=None):
    """UTC timestamps the agents need for today's PMH/PML and session VWAP."""
    local = local_now(cfg, now)
    day = local.date()

    def at(t):
        return dt.datetime.combine(day, t, tzinfo=tz(cfg)).astimezone(dt.timezone.utc)

    return {
        "session_date": day.isoformat(),
        "now_local": local.strftime("%Y-%m-%d %H:%M %Z"),
        "now_utc": (now or utcnow()).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "premarket_start_utc": at(PREMARKET_OPEN).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rth_open_utc": at(RTH_OPEN).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rth_close_utc": at(RTH_CLOSE).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---- journal ---------------------------------------------------------------
# One JSON line per order the guard saw. Written only by the guard hook, so the
# agents cannot edit their own trade counts.

def journal_path(day, journal_dir=JOURNAL_DIR):
    return Path(journal_dir) / f"{day.isoformat()}.jsonl"


def append_journal(record, day, journal_dir=JOURNAL_DIR):
    path = journal_path(day, journal_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_journal(day, journal_dir=JOURNAL_DIR):
    path = journal_path(day, journal_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def submitted(records, mode):
    kind = "paper_order" if mode == "paper" else "order"
    return [r for r in records if r.get("kind") == kind]


def entries_today(day, mode, journal_dir=JOURNAL_DIR):
    return sum(1 for r in submitted(read_journal(day, journal_dir), mode) if r["side"] == "buy")


def day_trades_last_5(day, mode, journal_dir=JOURNAL_DIR):
    """Approximate PDT count: symbols bought and sold on the same day, over the
    last 5 business days including today. Counts submitted orders, not fills."""
    count, cursor, seen_days = 0, day, 0
    while seen_days < 5:
        if cursor.weekday() < 5:
            seen_days += 1
            orders = submitted(read_journal(cursor, journal_dir), mode)
            bought = {r["symbol"] for r in orders if r["side"] == "buy"}
            sold = {r["symbol"] for r in orders if r["side"] == "sell"}
            count += len(bought & sold)
        cursor -= dt.timedelta(days=1)
    return count


def to_decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None
