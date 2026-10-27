import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.backtest import wilder_atr  # noqa: E402
from bot.strategy import Bar, DayContext, Params, SymbolStrategy, saty_levels  # noqa: E402

ET = ZoneInfo("America/New_York")


class SatyTests(unittest.TestCase):
    def test_levels(self):
        lv = saty_levels(100.0, 10.0)
        self.assertAlmostEqual(lv[0.236], 102.36)
        self.assertAlmostEqual(lv[-0.236], 97.64)
        self.assertAlmostEqual(lv[0.618], 106.18)
        self.assertAlmostEqual(lv[1.0], 110.0)
        self.assertEqual(saty_levels(100.0, None), {})

    def test_wilder_atr(self):
        day = dt.date(2026, 1, 1)
        rows = [(day, 0, 101.0, 99.0, 100.0)] * 20      # constant 2-point range
        self.assertAlmostEqual(wilder_atr(rows), 2.0)
        self.assertIsNone(wilder_atr(rows[:10]))

    def test_gate_arms_on_close_through_38_2(self):
        p = Params(setups=("SATY",), saty_entries=("gate",), saty_conviction=False,
                   require_daily_trend=False, max_risk_atr=10)
        s = SymbolStrategy("X", p)
        s.start_day(DayContext(pdc=100.0, daily_atr=10.0))
        start = dt.datetime(2026, 3, 2, 9, 30, tzinfo=ET)
        armed = None
        # Steady climb from 102 to just above the 38.2% level (103.82); ribbon stacks bullish.
        for i, c in enumerate([102.0 + 0.05 * k for k in range(37)] + [104.0]):
            t = start + dt.timedelta(minutes=5 * i)
            bar = Bar(t, c - 0.05, c + 0.02, c - 0.1, c, 1000)
            for kind, payload in s.on_5m_close(bar, t.strftime("%H:%M"), (t + dt.timedelta(minutes=5)).time(), 0.0):
                if kind == "arm":
                    armed = payload
        self.assertIsNotNone(armed)
        self.assertEqual(armed.level_name, "gate")
        self.assertAlmostEqual(armed.level, 103.82)
        self.assertGreater(armed.target, armed.trigger)


if __name__ == "__main__":
    unittest.main()
