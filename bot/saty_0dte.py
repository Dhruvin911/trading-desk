"""Saty's SPY day-trade plan with same-day (0DTE) options, in a small account.

    python3 -m bot.saty_0dte                          # $1,000 margin account (PDT), $100 a trade
    python3 -m bot.saty_0dte --account cash --max-cost 50 --no-vix
    python3 -m bot.saty_0dte --strike atm --trades out.csv

Signals are the desk playbook (bot.saty_desk) on SPY in both directions: the long rules on SPY
give calls, the same rules on SPY reflected around a price (bot.saty_desk.mirrored) give the
bearish mirror - put trigger, short golden gate, bearish ribbon, Vomy, bear flags - and puts.
As in Saty's daily plans:
- strike about one ATR level out of the money (--strike level1: the first target level, rounded
  away from the price to a whole dollar; atm: the first strike past the price)
- VIX key level (--no-vix to turn off): the VIX day-mode ATR level nearest the 9:00 ET VIX,
  rounded to the nearest 0.5 (his method in "Making a SPY Day Trade Plan"). Calls only while VIX is
  under it, puts only while VIX is over it.
- no trading on the monthly options expiration (third Friday)
- out by 15:40 ET (Robinhood closes 0DTE positions at 15:45)
Prices: Black-Scholes, implied vol = VIX x 0.8 for calls and x 1.0 for puts, which is what fits
SPY's real 770 call and 764 put on 2026-10-27 (data/options/SPY_0dte_2026-10-27.json); half the
bid/ask spread per fill = max($0.01, 1% of the premium). Robinhood keeps no intraday history for
expired contracts, so other days are modeled.
Size: as many contracts as --max-cost buys (Saty sizes short-dated options by premium, not by a
stop); scale-outs sell whole contracts.
"""
import argparse
import bisect
import csv
import datetime as dt
import json
import math
from argparse import Namespace
from collections import defaultdict

from bot.backtest import ET, SymbolData, wilder_atr
from bot.saty_desk import ROOT, mirrored, run_symbol
from bot.saty_options import bs_call, run_account
from bot.strategy import saty_levels


def bs_put(s, k, t, vol):
    return bs_call(s, k, t, vol) - s + k


def third_friday(day):
    d = day.replace(day=1)
    while d.weekday() != 4:
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=14)


class Vix:
    def __init__(self):
        self.ts, self.close = [], []
        with open(ROOT / "data" / "VIX_30m.csv") as f:
            for r in csv.DictReader(f):
                self.ts.append(dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) + dt.timedelta(minutes=30))
                self.close.append(float(r["close"]))
        self.daily = []
        with open(ROOT / "data" / "VIX_1d.csv") as f:
            for r in csv.DictReader(f):
                self.daily.append((dt.date.fromisoformat(r["ts"][:10]), float(r["open"]), float(r["high"]),
                                   float(r["low"]), float(r["close"])))

    def at(self, t):
        """Last completed 30-minute VIX close at time t."""
        i = bisect.bisect_right(self.ts, t) - 1
        return self.close[i] if i >= 0 else None

    def key_level(self, day):
        prior = [r for r in self.daily if r[0] < day]
        if len(prior) < 20:
            return None
        lv = saty_levels(prior[-1][4], wilder_atr(prior))
        now = self.at(dt.datetime.combine(day, dt.time(9, 0), tzinfo=ET))
        if now is None:
            return None
        nearest = min(lv.values(), key=lambda x: abs(x - now))
        return round(nearest * 2) / 2


def price(kind, s, k, ts, vix, o):
    close = dt.datetime.combine(ts.astimezone(ET).date(), dt.time(16, 0), tzinfo=ET)
    t = max(0.0, (close - ts).total_seconds()) / (365 * 86400)
    vol = vix / 100 * (o.call_iv if kind == "call" else o.put_iv)
    return bs_call(s, k, t, vol) if kind == "call" else bs_put(s, k, t, vol)


def to_option(t, kind, center, vix, o):
    """One underlying trade (prices in the reflected space for puts) as a 0DTE option trade."""
    real = (lambda p: p) if kind == "call" else (lambda p: 2 * center - p)
    opened = dt.datetime.fromisoformat(t["opened_at"])
    s0 = real(t["entry"])
    levels = [real(float(x.split("@")[1])) for x in t["targets"].split()]
    if o.strike == "level1":
        k = math.ceil(levels[0]) if kind == "call" else math.floor(levels[0])
    else:
        k = math.floor(s0) + 1 if kind == "call" else math.ceil(s0) - 1
    limit = levels[min(1, len(levels) - 1)]
    v0 = vix.at(opened)
    if v0 is None:
        return {"skip": "no VIX"}
    half = lambda p: max(0.01, o.spread * p)
    while True:
        prem = price(kind, s0, k, opened, v0, o)
        cost = prem + half(prem)
        if cost * 100 <= o.max_cost:
            break
        k += 1 if kind == "call" else -1
        if (kind == "call" and k > math.ceil(limit)) or (kind == "put" and k < math.floor(limit)):
            return {"skip": "too expensive"}
    if prem < o.min_premium:
        return {"skip": "premium too small"}
    n = int(o.max_cost // (cost * 100))
    pnl, sold, cum = -cost * 100 * n, 0, 0.0
    fills = json.loads(t["fills"])
    for i, (ts, px, frac, reason) in enumerate(fills):
        cum += frac
        target = n if i == len(fills) - 1 else min(n, round(cum * n))
        q = target - sold
        if q <= 0:
            continue
        when = dt.datetime.fromisoformat(ts)
        v = price(kind, real(px), k, when, vix.at(when) or v0, o)
        pnl += max(0.0, v - half(v)) * 100 * q
        sold += q
    return {"date": t["date"], "symbol": "SPY", "side": kind, "setup": t["setup"], "entry_time": t["entry_time"],
            "exit_time": t["exit_time"], "spy_entry": round(s0, 2), "strike": k, "vix": v0,
            "premium": round(cost, 2), "contracts": n, "cost_usd": round(cost * 100 * n, 2),
            "pnl_usd": round(pnl, 2), "return_pct": round(100 * pnl / (cost * 100 * n), 1),
            "stock_r": t["r"], "exit_reason": t["exit_reason"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 3, 9))
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--account", choices=["margin", "cash"], default="margin")
    ap.add_argument("--max-day-trades", type=int, default=3)
    ap.add_argument("--max-open", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=100.0, help="premium per trade, dollars")
    ap.add_argument("--min-premium", type=float, default=0.10)
    ap.add_argument("--strike", choices=["level1", "atm"], default="level1")
    ap.add_argument("--no-vix", dest="vix_filter", action="store_false")
    ap.add_argument("--side", choices=["both", "call", "put"], default="both")
    ap.add_argument("--call-iv", type=float, default=0.8, help="call implied vol = VIX x this")
    ap.add_argument("--put-iv", type=float, default=1.0, help="put implied vol = VIX x this")
    ap.add_argument("--spread", type=float, default=0.01)
    ap.add_argument("--max-per-day", type=int, default=2, help="signals per side per day")
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    cfg = json.loads((ROOT / "desk" / "config.json").read_text())
    cfg["flatten_at"] = "15:40"
    so = Namespace(start=o.start, end=o.end, slip=0.0, exit13="always", ivomy=True, max_per_symbol=o.max_per_day,
                   min_target_r=0.0, entries=["pullback", "retest", "break", "trendline", "orb", "flag"],
                   squeeze="none", daily_squeeze="none", sr_levels=True, structure_exit=True)
    spy = SymbolData(str(ROOT / "data"), "SPY")
    mir = mirrored(spy)
    vix = Vix()
    opt, skips = [], defaultdict(int)
    for kind, data in (("call", spy), ("put", mir)):
        if o.side not in ("both", kind):
            continue
        for t in run_symbol(str(ROOT / "data"), "SPY", cfg, so, data=data):
            day = dt.date.fromisoformat(t["date"])
            if day == third_friday(day):
                skips["monthly OPEX"] += 1
                continue
            if o.vix_filter:
                key = vix.key_level(day)
                now = vix.at(dt.datetime.fromisoformat(t["opened_at"]))
                if key is None or now is None or (kind == "call" and now >= key) or (kind == "put" and now <= key):
                    skips["VIX on the wrong side of its key level"] += 1
                    continue
            r = to_option(t, kind, mir.center, vix, o)
            if "skip" in r:
                skips[r["skip"]] += 1
            else:
                opt.append(r)
    opt.sort(key=lambda t: (t["date"], t["entry_time"]))
    taken, acct_skips, equity = run_account(opt, o)
    pnl = sum(t["pnl_usd"] for t in taken)
    peak, dd = o.balance, 0.0
    for _, v in equity:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    win = lambda ts: sum(t["pnl_usd"] > 0 for t in ts) / max(1, len(ts))
    print(f"SPY 0DTE, {o.account} account, start ${o.balance:,.0f}, ${o.max_cost:.0f} a trade: {len(taken)} trades, "
          f"win {win(taken):.0%}, P&L ${pnl:+,.0f}, end ${equity[-1][1]:,.0f}, max drawdown ${dd:,.0f}")
    for side in ("call", "put"):
        ts = [t for t in taken if t["side"] == side]
        if ts:
            print(f"  {side}s: {len(ts)} trades, win {win(ts):.0%}, P&L ${sum(t['pnl_usd'] for t in ts):+,.0f}, "
                  f"avg return {sum(t['return_pct'] for t in ts) / len(ts):+.1f}%")
    print(f"  every signal priced: {len(opt)}, win {win(opt):.0%}, avg ${sum(t['pnl_usd'] for t in opt) / max(1, len(opt)):+.2f}, "
          f"avg return {sum(t['return_pct'] for t in opt) / max(1, len(opt)):+.1f}%")
    print("  skipped signals:", dict(skips), "| skipped by the account:", dict(acct_skips))
    months = defaultdict(lambda: [0, 0.0])
    for t in taken:
        months[t["date"][:7]][0] += 1
        months[t["date"][:7]][1] += t["pnl_usd"]
    print("  by month: " + ", ".join(f"{m} {n} ${p:+,.0f}" for m, (n, p) in sorted(months.items())))
    if o.trades and opt:
        with open(o.trades, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(opt[0]))
            w.writeheader()
            w.writerows(taken)


if __name__ == "__main__":
    main()
