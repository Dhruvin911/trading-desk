import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desklib  # noqa: E402

spec = importlib.util.spec_from_file_location("order_guard", ROOT / ".claude" / "hooks" / "order_guard.py")
order_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(order_guard)

ET = dt.timezone(dt.timedelta(hours=-4))  # EDT, matches the October dates below


def et(day, hm):
    h, m = map(int, hm.split(":"))
    return dt.datetime(2026, 10, day, h, m, tzinfo=ET).astimezone(dt.timezone.utc)


def config(**overrides):
    cfg = desklib.load_config(ROOT / "desk" / "config.json")
    cfg["account_number"] = "123456789"
    cfg.update(overrides)
    return cfg


ON_LIVE = {"enabled": True, "mode": "live", "enabled_until": None, "killed": False}


def order(**inp):
    base = {"account_number": "123456789", "symbol": "MRVL", "side": "buy", "type": "limit",
            "quantity": "1", "limit_price": "240.00"}
    return {"tool_name": "mcp__robinhood-trading__place_equity_order", "agent_type": "trader",
            "tool_input": {**base, **inp}}


class PhaseTests(unittest.TestCase):
    def test_phases_through_the_day(self):
        cfg = config()
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "09:00")), "closed")
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "09:32")), "manage")
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "09:40")), "entries")
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "12:00")), "manage")
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "15:55")), "flatten")
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(27, "16:00")), "closed")

    def test_weekend_and_off(self):
        cfg = config()
        self.assertEqual(desklib.phase(cfg, ON_LIVE, et(31, "10:00")), "closed")  # Saturday
        self.assertEqual(desklib.phase(cfg, {**ON_LIVE, "enabled": False}, et(27, "10:00")), "off")
        self.assertEqual(desklib.phase(cfg, {**ON_LIVE, "killed": True}, et(27, "10:00")), "off")

    def test_enabled_until_expires(self):
        state = {**ON_LIVE, "enabled_until": et(27, "11:00").isoformat()}
        self.assertTrue(desklib.is_enabled(state, et(27, "10:59")))
        self.assertFalse(desklib.is_enabled(state, et(27, "11:00")))

    def test_session_times_in_utc(self):
        t = desklib.session_times(config(), et(27, "10:00"))
        self.assertEqual(t["premarket_start_utc"], "2026-10-27T08:00:00Z")
        self.assertEqual(t["rth_open_utc"], "2026-10-27T13:30:00Z")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name)
        self.cfg = config()

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, payload, state=ON_LIVE, when=None):
        when = when or et(27, "10:00")
        decision, reason, record = order_guard.decide(payload, self.cfg, state, when, self.journal)
        if record:
            desklib.append_journal(record, desklib.local_now(self.cfg, when).date(), self.journal)
        return decision, reason

    def test_valid_entry_passes(self):
        self.assertEqual(self.decide(order())[0], "pass")

    def test_options_and_crypto_always_blocked(self):
        for tool in ("place_option_order", "place_crypto_order", "exercise_option"):
            self.assertEqual(self.decide({"tool_name": f"mcp__x__{tool}", "tool_input": {}})[0], "deny")

    def test_entry_blocks(self):
        cases = {
            "off": (order(), {**ON_LIVE, "enabled": False}, et(27, "10:00")),
            "outside window": (order(), ON_LIVE, et(27, "12:00")),
            "not watchlist": (order(symbol="TSLA"), ON_LIVE, None),
            "market order": (order(type="market", limit_price=None, dollar_amount="100"), ON_LIVE, None),
            "too big": (order(quantity="2"), ON_LIVE, None),
            "fractional": (order(quantity="0.5"), ON_LIVE, None),
            "wrong account": (order(account_number="999"), ON_LIVE, None),
            "extended hours": (order(market_hours="extended_hours"), ON_LIVE, None),
            "killed": (order(), {**ON_LIVE, "killed": True}, None),
        }
        for name, (payload, state, when) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.decide(payload, state, when)[0], "deny")

    def test_only_trader_may_order(self):
        payload = order()
        payload.pop("agent_type")
        decision, reason = self.decide(payload)
        self.assertEqual(decision, "deny")
        self.assertIn("main session", reason)

    def test_exit_allowed_when_off_but_not_when_killed(self):
        sell = order(side="sell", type="stop_market", limit_price=None, stop_price="230")
        self.assertEqual(self.decide(sell, {**ON_LIVE, "enabled": False}, et(27, "15:55"))[0], "pass")
        self.assertEqual(self.decide(sell, {**ON_LIVE, "killed": True})[0], "deny")

    def test_paper_mode_records_and_blocks(self):
        decision, reason = self.decide(order(), {**ON_LIVE, "mode": "paper"})
        self.assertEqual(decision, "deny")
        self.assertIn("PAPER MODE", reason)
        day = dt.date(2026, 10, 27)
        self.assertEqual(desklib.entries_today(day, "paper", self.journal), 1)
        self.assertEqual(desklib.entries_today(day, "live", self.journal), 0)

    def test_daily_entry_cap(self):
        cap = self.cfg["risk"]["max_entries_per_day"]
        for _ in range(cap):
            self.assertEqual(self.decide(order(symbol="RKLB", limit_price="58"))[0], "pass")
        decision, reason = self.decide(order(symbol="RKLB", limit_price="58"))
        self.assertEqual(decision, "deny")
        self.assertIn("Daily entry cap", reason)

    def test_day_trade_cap_counts_last_five_business_days(self):
        # Round trips on Thu 22, Fri 23 and Mon 26; then a new entry on Tue 27 is blocked.
        for day in (22, 23, 26):
            when = et(day, "10:00")
            self.decide(order(symbol="RKLB", limit_price="50"), ON_LIVE, when)
            self.decide(order(symbol="RKLB", side="sell", limit_price="51"), ON_LIVE, when)
        self.assertEqual(desklib.day_trades_last_5(dt.date(2026, 10, 27), "live", self.journal), 3)
        decision, reason = self.decide(order())
        self.assertEqual(decision, "deny")
        self.assertIn("Day-trade cap", reason)


class HookProcessTests(unittest.TestCase):
    """Run the hooks the way Claude Code does: JSON on stdin, decision on stdout."""

    def run_hook(self, name, payload, env_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            # Copy the project skeleton the hook needs so the real journal is untouched.
            root = Path(tmp)
            (root / "desk").mkdir()
            cfg = config()
            (root / "desk" / "config.json").write_text(json.dumps(cfg))
            (root / "desklib.py").write_text((ROOT / "desklib.py").read_text())
            env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "DESK_ROOT": str(root), **(env_extra or {})}
            out = subprocess.run([sys.executable, str(ROOT / ".claude" / "hooks" / name)],
                                 input=json.dumps(payload), capture_output=True, text=True, env=env)
            return out.stdout, root

    def test_order_guard_denies_when_desk_off(self):
        stdout, _ = self.run_hook("order_guard.py", order())
        self.assertEqual(json.loads(stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_write_guard(self):
        root_cfg = {"tool_name": "Write", "tool_input": {"file_path": "desk/config.json"}}
        notes = {"tool_name": "Write", "tool_input": {"file_path": "desk/notes/plans/MRVL.json"}}
        self.assertEqual(self.run_hook("write_guard.py", root_cfg)[0], "")  # inactive outside cycles
        self.assertIn("deny", self.run_hook("write_guard.py", root_cfg, {"DESK_AGENT": "1"})[0])
        self.assertEqual(self.run_hook("write_guard.py", notes, {"DESK_AGENT": "1"})[0], "")


if __name__ == "__main__":
    unittest.main()
