"""Saty's "GG reload" on SPY with same-day options, plus his Phase Oscillator.

    python3 -m bot.saty_ggreload                         # Mar 9 - Oct 27, $1,000 margin account
    python3 -m bot.saty_ggreload --day 2026-10-27 -v     # one day, candle by candle
    python3 -m bot.saty_ggreload --entry trigger --vix

The setup, as in his SPX trade of 2026-10-27 ("GG unlocked" ... "3m extreme. PO needs to cool" ...
"GG back in play if we can recover 3m 21 and get back over long trigger. Ideal would be PDC
resolution" ... "Entered 7720c expecting PDC resolution using a loss of PDC as risk" ...
"Exited 7720c GG complete"):
1. the golden gate unlocks: a candle reaches +38.2% ATR from the previous close (PDC)
2. it cools: the Phase Oscillator drops to --cool (23.6, the neutral zone) or a candle closes
   under the 21 EMA
3. PDC holds: no candle has closed under PDC since the unlock (if one has, the setup is off)
4. entry: the first close back above the 21 EMA (--entry reclaim21), or back above the call
   trigger (--entry trigger), while above PDC and below the gate; buy-stop above that candle
5. risk: a candle close under PDC ends it; a hard stop sits --stop-atr daily ATRs under PDC
6. the option: same-day call at the golden-fib strike (+61.8%, rounded up); scale out equally at
   the call trigger, the gate and the mid that are above the entry, the rest at the golden fib
   ("GG complete"); out by 15:40
The bearish mirror (gate at -38.2%, puts at the -61.8% strike) runs the same rules on SPY reflected
around a price (bot.saty_desk.mirrored). Candles are 3-minute where 1-minute bars exist (from
Sep 14, 2026), otherwise 5-minute. Option prices as in bot.saty_0dte (Black-Scholes on VIX, fitted
to SPY's real 0DTE prices on 2026-10-27).
"""
import argparse
import datetime as dt
import json
import math
from collections import defaultdict

from bot.backtest import ET, RTH_CLOSE, RTH_OPEN, SymbolData, wilder_atr
from bot.saty_0dte import Vix, price, third_friday
from bot.saty_desk import ROOT, mirrored
from bot.saty_options import run_account
from bot.strategy import ATR, EMA, saty_levels
from bot.timewarp import bars_by_period


class PhaseOscillator:
    """Saty Phase Oscillator (Pine source in his indicator): pivot = 21 EMA, raw = (close - pivot) /
    (3 x ATR(14)) x 100, oscillator = 3 EMA of raw; compression when the 2-sd Bollinger band is
    inside 2 ATR of the pivot, released once it expands past 1.854 ATR while widening."""

    def __init__(self):
        self.pivot, self.atr, self.smooth = EMA(21), ATR(14), EMA(3)
        self.closes, self.prev_compression = [], None
        self.value = self.prev_value = None
        self.compressed = False

    def update(self, bar):
        p, a = self.pivot.update(bar.c), self.atr.update(bar)
        self.closes = (self.closes + [bar.c])[-21:]
        m = sum(self.closes) / len(self.closes)
        sd = (sum((x - m) ** 2 for x in self.closes) / len(self.closes)) ** 0.5
        above = bar.c >= p
        bb_up, bb_dn = p + 2 * sd, p - 2 * sd
        comp = (bb_up - (p + 2 * a)) if above else ((p - 2 * a) - bb_dn)
        in_exp = (bb_up - (p + 1.854 * a)) if above else ((p - 1.854 * a) - bb_dn)
        expanding = self.prev_compression is not None and self.prev_compression <= comp
        self.compressed = False if (expanding and in_exp > 0) else comp <= 0
        self.prev_compression = comp
        self.prev_value = self.value
        self.value = self.smooth.update((bar.c - p) / (3 * a) * 100 if a else 0.0)
        return self.value

    def crossed_down(self, level):
        """A "yellow light": the oscillator just fell back through `level` (61.8 or 100)."""
        return self.prev_value is not None and self.prev_value >= level > self.value


def run_side(d, o, kind, verbose=False):
    """Underlying trades of one side (d is SPY, or SPY mirrored for puts). Prices in d's space."""
    po, trades = PhaseOscillator(), []
    for day in sorted(d.m5):
        m1 = [(t, b) for t, b, _ in d.m1.get(day, []) if RTH_OPEN <= t.time() < RTH_CLOSE]
        if sum(b.v for _, b in m1) > 0 and len(m1) >= 300:
            fine, step, cmin = m1, dt.timedelta(minutes=1), 3
            candles = {c[1]: c for c in bars_by_period(m1, 3)}
        else:
            fine, step, cmin = d.rth(day), dt.timedelta(minutes=5), 5
            candles = {t + step: (t, t + step, b) for t, b in fine}
        prior = [r for r in d.daily if r[0] < day]
        if (o.start and day < o.start) or (o.end and day > o.end) or day == third_friday(day) or len(prior) < 20:
            for _, _, cb in candles.values():                   # keep the oscillator warm on skipped days
                po.update(cb)
            continue
        pdc, atr = prior[-1][4], wilder_atr(prior)
        lv = saty_levels(pdc, atr)
        trig, gate, mid, fib = lv[0.236], lv[0.382], lv[0.5], lv[0.618]
        unlocked = cooled = broken = traded = False
        prev_close = prev_pivot = None
        armed = pos = None
        for t, b in fine:
            end = t + step
            lt = end.astimezone(ET).time()
            # fills, stop and targets on this bar
            if armed and pos is None and b.h >= armed["entry"]:
                fill = max(armed["entry"], b.o)
                pos = {"entry": fill, "stop": armed["stop"], "opened": t, "fills": [], "left": 1.0,
                       "targets": [x for x in armed["targets"] if x[1] > fill], "risk": fill - armed["stop"]}
                if not pos["targets"]:
                    pos["targets"] = [("+61.8%", fib, 1.0)]
                n = len(pos["targets"])
                pos["targets"] = [(nm, px, 1.0 / n) for nm, px, _ in pos["targets"]]
                armed, traded = None, True
            if pos:
                if b.l <= pos["stop"]:
                    pos["fills"].append((t, min(pos["stop"], b.o), pos["left"], "stop under PDC"))
                    pos["left"] = 0
                else:
                    while pos["targets"] and b.h >= pos["targets"][0][1]:
                        nm, px, fr = pos["targets"].pop(0)
                        q = fr if pos["targets"] else pos["left"]
                        pos["fills"].append((t, max(px, b.o), q, "golden fib: GG complete" if not pos["targets"] else "target " + nm))
                        pos["left"] -= q
                if lt >= dt.time(15, 40) and pos["left"] > 1e-9:
                    pos["fills"].append((t, b.c, pos["left"], "flatten 15:40"))
                    pos["left"] = 0
                if pos["left"] <= 1e-9:
                    trades.append(close_trade(pos, day, kind))
                    pos = None
            c = candles.get(end)
            if c is None:
                continue
            _, _, cb = c
            v = po.update(cb)
            piv = po.pivot.value
            if pos and cb.c < pdc:
                pos["fills"].append((end, cb.c, pos["left"], "candle close under PDC"))
                pos["left"] = 0
                trades.append(close_trade(pos, day, kind))
                pos = None
            if armed and end >= armed["expires"]:
                armed = None
            if cb.h >= gate:
                unlocked = True
            if unlocked and (v <= o.cool or cb.c < piv):
                cooled = True
            if unlocked and cb.c < pdc:
                broken = True
            if verbose:
                print(f"{lt:%H:%M} c {cb.c:8.2f} 21 {piv:8.2f} PO {v:6.1f}{' C' if po.compressed else '  '} "
                      f"{'unlocked ' if unlocked else ''}{'cooled ' if cooled else ''}{'PDC lost ' if broken else ''}")
            reclaim = (prev_close is not None and prev_close < prev_pivot and cb.c >= piv) if o.entry == "reclaim21" \
                else (prev_close is not None and prev_close <= trig < cb.c)
            prev_close, prev_pivot = cb.c, piv
            if (pos or armed or traded or broken or not (unlocked and cooled) or not reclaim
                    or not (o.first <= lt <= o.last) or not (pdc < cb.c < gate) or v >= 100):
                continue
            if o.vix_filter and not o.vix_ok(kind, end, day):
                continue
            stop = pdc - o.stop_atr * atr
            targets = [(nm, px, 0) for nm, px in (("call trigger", trig), ("golden gate", gate), ("mid", mid), ("+61.8%", fib))]
            armed = {"entry": round(cb.h + 0.01, 2), "stop": stop, "targets": targets,
                     "expires": end + dt.timedelta(minutes=2 * cmin)}
            if verbose:
                print(f"      -> armed: buy-stop {armed['entry']:.2f}, stop {stop:.2f}, golden fib {fib:.2f}")
        if pos:
            pos["fills"].append((fine[-1][0], fine[-1][1].c, pos["left"], "end of day"))
            trades.append(close_trade(pos, day, kind))
    return trades


def close_trade(pos, day, kind):
    r = sum(q * (px - pos["entry"]) for _, px, q, _ in pos["fills"]) / pos["risk"]
    return {"date": day.isoformat(), "side": kind, "opened": pos["opened"], "entry": pos["entry"],
            "stop": pos["stop"], "fills": pos["fills"], "r": r, "exit_reason": pos["fills"][-1][3]}


def as_option(t, center, vix, o, fib_real):
    kind = t["side"]
    real = (lambda p: p) if kind == "call" else (lambda p: 2 * center - p)
    s0 = real(t["entry"])
    k = math.ceil(fib_real) if kind == "call" else math.floor(fib_real)
    if o.strike == "atm":
        k = math.ceil(s0) if kind == "call" else math.floor(s0)
    v0 = vix.at(t["opened"])
    half = lambda p: max(0.01, o.spread * p)
    prem = price(kind, s0, k, t["opened"], v0, o)
    cost = prem + half(prem)
    if prem < o.min_premium or cost * 100 > o.max_cost:
        return None
    n = int(o.max_cost // (cost * 100))
    pnl, sold, cum = -cost * 100 * n, 0, 0.0
    for i, (ts, px, frac, reason) in enumerate(t["fills"]):
        cum += frac
        target = n if i == len(t["fills"]) - 1 else min(n, round(cum * n))
        q = target - sold
        if q <= 0:
            continue
        val = price(kind, real(px), k, ts, vix.at(ts) or v0, o)
        pnl += max(0.0, val - half(val)) * 100 * q
        sold += q
    return {"date": t["date"], "side": kind, "entry_time": t["opened"].astimezone(ET).strftime("%H:%M"),
            "exit_time": t["fills"][-1][0].astimezone(ET).strftime("%H:%M"), "spy_entry": round(s0, 2), "strike": k,
            "premium": round(cost, 2), "contracts": n, "cost_usd": round(cost * 100 * n, 2), "pnl_usd": round(pnl, 2),
            "return_pct": round(100 * pnl / (cost * 100 * n), 1), "stock_r": round(t["r"], 3), "exit_reason": t["exit_reason"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 3, 9))
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--day", type=dt.date.fromisoformat, help="one day; with -v prints every candle")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--entry", choices=["reclaim21", "trigger"], default="reclaim21")
    ap.add_argument("--cool", type=float, default=23.6, help="Phase Oscillator level that counts as cooled")
    ap.add_argument("--stop-atr", type=float, default=0.05, help="hard stop this many daily ATRs under PDC")
    ap.add_argument("--first", type=dt.time.fromisoformat, default=dt.time(9, 40))
    ap.add_argument("--last", type=dt.time.fromisoformat, default=dt.time(13, 0))
    ap.add_argument("--vix", dest="vix_filter", action="store_true", help="calls only under the VIX key level, puts over")
    ap.add_argument("--strike", choices=["fib", "atm"], default="fib")
    ap.add_argument("--side", choices=["both", "call", "put"], default="both")
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--account", choices=["margin", "cash"], default="margin")
    ap.add_argument("--max-day-trades", type=int, default=3)
    ap.add_argument("--max-open", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=100.0)
    ap.add_argument("--min-premium", type=float, default=0.05)
    ap.add_argument("--call-iv", type=float, default=0.8)
    ap.add_argument("--put-iv", type=float, default=1.0)
    ap.add_argument("--spread", type=float, default=0.01)
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    if o.day:
        o.start = o.end = o.day
    spy = SymbolData(str(ROOT / "data"), "SPY")
    mir = mirrored(spy)
    vix = Vix()
    keys = {}

    def vix_ok(kind, when, day):
        if day not in keys:
            keys[day] = vix.key_level(day)
        now = vix.at(when)
        return keys[day] is not None and now is not None and (now < keys[day] if kind == "call" else now > keys[day])
    o.vix_ok = vix_ok
    opts, under = [], []
    for kind, data in (("call", spy), ("put", mir)):
        if o.side not in ("both", kind):
            continue
        if o.verbose:
            print(f"== {kind}s")
        for t in run_side(data, o, kind, o.verbose):
            under.append(t)
            day = dt.date.fromisoformat(t["date"])
            prior = [r for r in spy.daily if r[0] < day]
            fib_real = prior[-1][4] + (0.618 if kind == "call" else -0.618) * wilder_atr(prior)
            r = as_option(t, mir.center, vix, o, fib_real)
            if r:
                opts.append(r)
    opts.sort(key=lambda t: (t["date"], t["entry_time"]))
    taken, skipped, equity = run_account(opts, o)
    rs = [t["r"] for t in under]
    print(f"setups: {len(under)} (calls {sum(t['side'] == 'call' for t in under)}, puts {sum(t['side'] == 'put' for t in under)}), "
          f"SPY move avg {sum(rs) / max(1, len(rs)):+.2f}R, win {sum(r > 0.05 for r in rs) / max(1, len(rs)):.0%}")
    pnl = sum(t["pnl_usd"] for t in taken)
    peak, dd = o.balance, 0.0
    for _, v in equity:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    print(f"options, {o.account} account, ${o.balance:,.0f}, ${o.max_cost:.0f} a trade: {len(taken)} trades, "
          f"win {sum(t['pnl_usd'] > 0 for t in taken) / max(1, len(taken)):.0%}, P&L ${pnl:+,.0f}, end ${equity[-1][1]:,.0f}, "
          f"max drawdown ${dd:,.0f}; skipped by the account: {dict(skipped)}")
    if o.verbose or o.day:
        for t in taken:
            print(" ", t)
    if o.trades and taken:
        import csv
        with open(o.trades, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(taken[0]))
            w.writeheader()
            w.writerows(taken)


if __name__ == "__main__":
    main()
