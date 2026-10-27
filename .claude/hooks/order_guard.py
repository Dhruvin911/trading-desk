#!/usr/bin/env python3
"""PreToolUse guard for Robinhood order tools.

These are the hard limits. The agents are told the same rules in their
prompts, but this hook is what actually stops an order, whatever the model
decides. Every order it sees is written to desk/journal/<date>.jsonl.

  - option / crypto orders and exercises: always blocked
  - kill switch on: every order blocked
  - only the configured agentic account, only the `trader` subagent
  - SELL (exits): allowed whenever the kill switch is off
  - BUY (entries): desk on, inside an entry window, watchlist symbol, limit or
    stop_limit, whole shares, regular hours, under the position cap, under the
    daily entry cap and the 5-day day-trade cap
  - paper mode: the order is recorded and then blocked, never sent
"""
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, os.environ.get("CLAUDE_PROJECT_DIR") or str(Path(__file__).resolve().parents[2]))
import desklib  # noqa: E402

ALWAYS_BLOCKED = {"place_option_order", "place_crypto_order", "exercise_option"}


def decide(payload, cfg, state, now, journal_dir=desklib.JOURNAL_DIR):
    """Return (decision, reason, record). decision is 'deny' or 'pass'.

    record is the journal line to write, or None."""
    tool = payload.get("tool_name", "").rsplit("__", 1)[-1]
    inp = payload.get("tool_input") or {}

    if tool in ALWAYS_BLOCKED:
        return "deny", f"{tool} is disabled: the desk trades stocks only.", None
    if tool != "place_equity_order":
        return "pass", "", None

    mode = state.get("mode", "paper")
    day = desklib.local_now(cfg, now).date()
    symbol = str(inp.get("symbol", "")).upper()
    side = str(inp.get("side", "")).lower()
    order_type = str(inp.get("type", "")).lower()
    agent = payload.get("agent_type") or payload.get("subagent_type")
    record = {
        "ts": now.isoformat(), "mode": mode, "agent": agent, "symbol": symbol, "side": side,
        "type": order_type, "quantity": inp.get("quantity"), "limit_price": inp.get("limit_price"),
        "stop_price": inp.get("stop_price"), "time_in_force": inp.get("time_in_force"),
    }

    def deny(reason):
        return "deny", reason, {**record, "kind": "blocked", "reason": reason}

    if state.get("killed"):
        return deny("Kill switch is on. No orders until `desk unkill`.")
    if not cfg.get("account_number"):
        return deny("account_number is not set in desk/config.json.")
    if str(inp.get("account_number")) != str(cfg["account_number"]):
        return deny("Orders are only allowed on the configured agentic account.")
    if cfg.get("require_trader_agent", True) and agent != "trader":
        return deny(f"Only the trader subagent may place orders (caller: {agent or 'main session'}).")
    if side not in ("buy", "sell"):
        return deny(f"Unknown side {side!r}.")

    if side == "buy":
        reason = check_entry(inp, cfg, state, now, day, symbol, order_type, mode, journal_dir)
        if reason:
            return deny(reason)

    if mode == "paper":
        return ("deny",
                "PAPER MODE: order recorded in the journal and NOT sent to Robinhood. "
                "Treat it as filled at its limit/stop price and track it in desk/notes/plans.",
                {**record, "kind": "paper_order"})
    return "pass", "", {**record, "kind": "order"}


def check_entry(inp, cfg, state, now, day, symbol, order_type, mode, journal_dir):
    risk = cfg["risk"]
    if not desklib.is_enabled(state, now):
        return "Desk is off: no new entries."
    if not desklib.in_entry_window(cfg, now):
        return "Outside the entry windows: no new entries."
    if cfg.get("restrict_to_watchlist", True) and symbol not in {s.upper() for s in cfg["watchlist"]}:
        return f"{symbol} is not on the watchlist."
    if order_type not in risk["allowed_entry_types"]:
        return f"Entry type {order_type!r} not allowed; use {risk['allowed_entry_types']}."
    if inp.get("dollar_amount"):
        return "Dollar-amount entries are not allowed; use a whole-share quantity."
    if inp.get("market_hours", "regular_hours") != "regular_hours":
        return "Entries are regular hours only."
    try:
        qty = Decimal(str(inp.get("quantity")))
        price = Decimal(str(inp.get("limit_price")))
    except (InvalidOperation, TypeError):
        return "Entries need a numeric quantity and limit_price."
    if qty <= 0 or qty != qty.to_integral_value():
        return "Entries must be a positive whole number of shares."
    notional = qty * price
    if notional > Decimal(str(risk["max_position_usd"])):
        return f"Position ${notional:.2f} is over the ${risk['max_position_usd']} cap."
    if desklib.entries_today(day, mode, journal_dir) >= risk["max_entries_per_day"]:
        return f"Daily entry cap ({risk['max_entries_per_day']}) reached."
    if desklib.day_trades_last_5(day, mode, journal_dir) >= risk["max_day_trades_5d"]:
        return f"Day-trade cap ({risk['max_day_trades_5d']} per 5 business days) reached."
    return None


def main():
    payload = json.load(sys.stdin)
    cfg = desklib.load_config()
    state = desklib.load_state()
    now = desklib.utcnow()
    decision, reason, record = decide(payload, cfg, state, now)
    if record:
        desklib.append_journal(record, desklib.local_now(cfg, now).date())
    if decision == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail closed: a broken guard must not let orders through
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Order guard error, order blocked: {exc}",
        }}))
