import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.backtest import wilder_atr  # noqa: E402
from bot.strategy import Bar, DayContext, Params, SymbolStrategy, check_price, find_flag, saty_levels  # noqa: E402

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

    def test_bull_and_bear_flag(self):
        t = dt.datetime(2026, 3, 2, 10, 0, tzinfo=ET)
        p = Params()
        pole = [Bar(t, c - 1, c, c - 1, c, 5000) for c in (100, 101, 102, 103, 104)]
        flag = [Bar(t, 103.6, 103.8, 103.2, 103.4, 1000), Bar(t, 103.4, 103.6, 103.0, 103.3, 1000)]
        self.assertEqual(find_flag(pole + flag, atr=1.0, p=p, bull=True), (103.8, 103.0))
        self.assertIsNone(find_flag(pole + flag, atr=3.0, p=p, bull=True))     # pole too small
        mirror = [Bar(t, 200 - b.o, 200 - b.l, 200 - b.h, 200 - b.c, b.v) for b in pole + flag]
        self.assertEqual(find_flag(mirror, atr=1.0, p=p, bull=False), (96.2, 97.0))

    def test_second_target_sells_the_rest(self):
        p = Params(setups=("SATY",))
        s = SymbolStrategy("X", p)
        s.start_day(DayContext(pdc=100.0, daily_atr=10.0))
        t = dt.datetime(2026, 3, 2, 11, 0, tzinfo=ET)
        from bot.strategy import ArmedEntry
        s.armed = ArmedEntry("X", "SATY", "gate_touch", 103.82, 103.83, 102.0, 99, t, target=106.18, target2=107.86)
        check_price(s, Bar(t, 103.5, 103.9, 103.4, 103.85, 1), p)
        self.assertIsNotNone(s.position)
        events = check_price(s, Bar(t, 104, 108.0, 104, 107.9, 1), p)
        self.assertEqual([e[0] for e in events], ["trim", "closed"])
        self.assertEqual(events[1][1].fills[-1][4], "second target")

    def test_saty_day_arms_on_10m_pullback_above_trigger(self):
        p = Params(setups=("SATYD",))
        s = SymbolStrategy("X", p)
        start = dt.datetime(2026, 3, 2, 9, 30, tzinfo=ET)
        # Warm the 10m EMAs on a slow uptrend, then a new day opens above the call trigger.
        price = 90.0
        for day in range(3):
            s.start_day(DayContext(pdc=price, daily_atr=40.0))
            for i in range(78):
                price += 0.02
                t = start + dt.timedelta(days=day, minutes=5 * i)
                s.on_5m_close(Bar(t, price - 0.01, price + 0.02, price - 0.02, price, 1000),
                              t.strftime("%H:%M"), (t + dt.timedelta(minutes=5)).time(), 0.0)
        s.start_day(DayContext(pdc=price - 10.0, daily_atr=40.0))   # trigger = pdc + 9.44
        armed = []
        for i in range(30):
            price += 0.02
            t = start + dt.timedelta(days=3, minutes=5 * i)
            low = price - (0.5 if i == 21 else 0.02)                # i = 20/21: the pullback 10m bar
            for kind, payload in s.on_5m_close(Bar(t, price - 0.01, price + 0.02, low, price, 1000),
                                               t.strftime("%H:%M"), (t + dt.timedelta(minutes=5)).time(), 0.0):
                if kind == "arm":
                    armed.append(payload)
        self.assertEqual(len(armed), 1)
        self.assertEqual(armed[0].setup, "SATYD")
        self.assertAlmostEqual(armed[0].target, price - 0.6 - 10.0 + 0.618 * 40.0, places=0)

    def test_timewarp_bars(self):
        from bot.timewarp import bars_by_period
        t = dt.datetime(2026, 3, 2, 9, 30, tzinfo=ET)
        rows = [(t + dt.timedelta(minutes=i), Bar(t, 100 + i, 101 + i, 99 + i, 100.5 + i, 10)) for i in range(7)]
        three = bars_by_period(rows, 3)
        self.assertEqual(len(three), 3)                     # 9:30, 9:33, 9:36 (partial)
        t0, close_t, b = three[0]
        self.assertEqual((t0.minute, close_t.minute), (30, 33))
        self.assertEqual((b.o, b.h, b.l, b.c, b.v), (100, 103, 99, 102.5, 30))


if __name__ == "__main__":
    unittest.main()
