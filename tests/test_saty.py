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

    def test_saty_desk_runs_on_real_day(self):
        import json
        from argparse import Namespace
        from bot.saty_desk import ROOT, run_symbol
        cfg = json.loads((ROOT / "desk" / "config.json").read_text())
        opts = Namespace(start=dt.date(2026, 10, 27), end=dt.date(2026, 10, 27), slip=0.0003, exit13="always",
                         ivomy=True, max_per_symbol=2, min_target_r=0.0,
                         entries=["pullback", "retest", "break", "trendline", "orb", "flag"], squeeze="none", daily_squeeze="none",
                         sr_levels=True, structure_exit=True)
        trades = run_symbol(str(ROOT / "data"), "RKLB", cfg, opts)
        self.assertTrue(trades)
        for t in trades:
            self.assertEqual(t["date"], "2026-10-27")
            self.assertGreater(t["entry"], t["stop"])

    def test_ttm_squeeze(self):
        from bot.saty_desk import ttm_squeeze
        flat = [100 + 0.01 * (i % 2) for i in range(40)]                 # tight coil
        on, mom = ttm_squeeze(flat, [x + 0.5 for x in flat], [x - 0.5 for x in flat], [1.0] * 40)
        self.assertTrue(on)
        up = [100 + i for i in range(40)]                                # strong trend: bands wide, momentum up
        on, mom = ttm_squeeze(up, [x + 0.2 for x in up], [x - 0.2 for x in up], [1.0] * 40)
        self.assertFalse(on)
        self.assertGreater(mom, 0)

    def test_phase_oscillator(self):
        from bot.saty_ggreload import PhaseOscillator
        po = PhaseOscillator()
        for i in range(60):                                              # steady climb: above the pivot, positive
            v = po.update(Bar(None, 100 + i, 100.5 + i, 99.5 + i, 100.2 + i, 1000))
        self.assertGreater(v, 23.6)
        for i in range(10):                                              # sharp drop back through the 21 EMA
            po.update(Bar(None, 150 - 3 * i, 150.5 - 3 * i, 147 - 3 * i, 148 - 3 * i, 1000))
        self.assertLess(po.value, 0)
        flat = PhaseOscillator()
        for i in range(60):                                              # tiny range: Bollinger inside 2 ATR, compressed
            flat.update(Bar(None, 100, 101, 99, 100 + 0.01 * (i % 2), 1000))
        self.assertTrue(flat.compressed)

    def test_gg_reload_oct27(self):
        from argparse import Namespace
        from bot.backtest import SymbolData
        from bot.saty_desk import ROOT
        from bot.saty_ggreload import run_side
        o = Namespace(start=dt.date(2026, 10, 27), end=dt.date(2026, 10, 27), entry="reclaim21", cool=23.6,
                      stop_atr=0.05, first=dt.time(9, 40), last=dt.time(13, 0), vix_filter=False)
        (t,) = run_side(SymbolData(str(ROOT / "data"), "SPY"), o, "call")     # Saty's SPX trade that day, on SPY
        self.assertEqual(t["exit_reason"], "golden fib: GG complete")
        self.assertGreater(t["r"], 1)

    def test_orb_paper_trade(self):
        from bot.orb_paper import day_trade
        t0 = dt.datetime(2026, 10, 27, 9, 35)
        step = dt.timedelta(minutes=5)
        up = [(t0, Bar(t0, 100.0, 101.2, 99.9, 101.0, 1)), (t0 + step, Bar(t0 + step, 101.0, 103.0, 100.9, 102.5, 1))]
        entry, exit_, _, _, why = day_trade(up, 1, 101.0, 0.5, step)        # buy-stop at the 5-minute high
        self.assertEqual((entry, exit_, why), (101.0, 102.5, "close"))
        down = [(t0, Bar(t0, 100.0, 101.2, 99.0, 99.2, 1))]                 # red bar: high (fill) first, then the low
        entry, exit_, _, _, why = day_trade(down, 1, 101.0, 0.5, step)
        self.assertEqual((entry, exit_, why), (101.0, 100.5, "stop"))
        self.assertIsNone(day_trade(down, 1, 102.0, 0.5, step))           # never reached the level

    def test_orb_saty_simulate(self):
        from bot.orb_saty import simulate
        t0 = dt.datetime(2026, 10, 27, 9, 45, tzinfo=ZoneInfo("America/New_York"))
        step = dt.timedelta(minutes=5)
        bars = [(t0, Bar(t0, 100.0, 101.5, 99.9, 101.4, 1)), (t0 + step, Bar(t0 + step, 101.4, 104.0, 101.3, 103.5, 1))]
        rib = [(t0 - step, {8: 100.5, 13: 100.3, 21: 100.0, 34: 99.5, 48: 99.0}, 100.8)]
        s = {"symbol": "X", "date": t0.date(), "side": 1, "or_dir": 1, "relvol": 2.0, "atr": 5.0, "or_hi": 101.0,
             "or_lo": 99.0, "levels": [101.18, 101.91, 102.5, 103.09, 103.93, 105.0], "trigger": 101.18,
             "bars": bars, "step": step, "ribbon": rib}
        p = {"direction": "candle", "relvol": True, "entry": "trigger", "confirm": "stack", "stop": "atr10",
             "exit": "eod", "last": "12:00"}
        t = simulate(s, p)                                               # buy-stop at the call trigger, held to the close
        self.assertAlmostEqual(t["entry"], 101.18)
        self.assertAlmostEqual(t["r"], (103.5 - 101.18) / 0.5)
        rib[0][1][8] = 99.0                                              # ribbon not stacked: no trade
        self.assertIsNone(simulate(s, p))

    def test_orb_options_pricing(self):
        from bot.orb_options import UNIVERSE, delta, expiry_after, price
        self.assertEqual(expiry_after(dt.date(2026, 10, 27), 7), dt.date(2026, 11, 6))      # first Friday 7+ days out
        t = 7 / 365
        self.assertAlmostEqual(delta("call", 100, 100, t, 0.5) - delta("put", 100, 100, t, 0.5), 1.0)
        self.assertAlmostEqual(price("call", 100, 100, t, 0.5) - price("put", 100, 100, t, 0.5), 0.0)  # put-call parity, r = 0
        self.assertNotIn("HIMS", UNIVERSE["spread"])                                         # real spread 11.2% of the mid
        self.assertIn("SPY", UNIVERSE["checklist"])

    def test_orb_saty_5m_ribbon(self):
        from bot.orb_saty import confirmed, simulate
        up = {8: 101.0, 13: 100.5, 21: 100.0, 34: 99.5, 48: 99.0}
        held = [(None, up, 101.0), (None, up, 100.8), (None, up, 101.2)]
        self.assertTrue(confirmed("hold13", None, held))
        self.assertFalse(confirmed("hold13", None, held[:2] + [(None, up, 100.4)]))     # a close under the 13
        self.assertFalse(confirmed("hold13", None, held[:2]))                            # not enough history
        tz = ZoneInfo("America/New_York")
        t0 = dt.datetime(2026, 10, 27, 9, 45, tzinfo=tz)
        step = dt.timedelta(minutes=5)
        bars = [(t0, Bar(t0, 100.0, 101.5, 99.9, 101.4, 1)), (t0 + step, Bar(t0 + step, 101.4, 101.6, 100.6, 100.7, 1)),
                (t0 + 2 * step, Bar(t0 + 2 * step, 100.7, 103.0, 100.6, 102.9, 1))]
        rib5 = [(t0, up, 101.0), (t0 - step, up, 100.8), (t0 - 2 * step, up, 101.2)][::-1]
        rib5.append((t0 + 2 * step, {8: 101.2, 13: 100.9, 21: 100.5, 34: 100.0, 48: 99.5}, 100.7))  # 9:55 close under 13
        s = {"symbol": "X", "date": t0.date(), "side": 1, "or_dir": 1, "relvol": 2.0, "atr": 5.0, "or_hi": 101.0,
             "or_lo": 99.0, "levels": [101.18, 101.91, 102.5, 103.09, 103.93, 105.0], "trigger": 101.18,
             "bars": bars, "step": step, "ribbon": [], "ribbon5": rib5}
        p = {"direction": "candle", "relvol": True, "entry": "orb", "confirm": "hold13", "stop": "or_mid",
             "exit": "c13", "last": "12:00"}
        t = simulate(s, p)                                                # out on the 5m close under the 13
        self.assertEqual(t["reason"], "5m close under 13")
        self.assertAlmostEqual(t["r"], (100.7 - 101.0) / 1.0)


if __name__ == "__main__":
    unittest.main()
