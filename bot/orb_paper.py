"""5-minute opening range breakout on "stocks in play", as in Zarattini, Barbon & Aziz (2024),
"A Profitable Day Trading Strategy for the U.S. Equity Market" (SSRN 4729284).

    python3 -m bot.orb_paper                       # the paper's rules on the 17 desk tickers, $25,000
    python3 -m bot.orb_paper --no-relvol           # their "base" strategy (no relative-volume filter)
    python3 -m bot.orb_paper --small               # $1,000 cash account: long only, no leverage

Rules (section 2.1 and 4 of the paper):
- the first 5-minute candle (9:30-9:35 ET) sets the direction: green -> buy-stop at its high,
  red -> sell-stop at its low, doji -> nothing; one trade per stock per day
- stop 10% of the 14-day ATR from the fill; otherwise out at the close (16:00)
- filters: open > $5, 14-day average volume >= 1M shares, ATR(14) > $0.50,
  relative volume of the first 5 minutes (vs its 14-day average) >= 100%, top --top by it
- size: lose 1% of capital at the stop, total leverage at most 4x (split over that day's
  candidates); commission $0.0035 a share
Fills: buy-stop at max(level, bar open), stops likewise; on 1-minute bars where real ones exist
(from 2026-09-14), otherwise 5-minute bars with the open-low-high-close / open-high-low-close path.
The paper's universe is ~7,000 stocks; here it is the desk's 17 tickers, so "top 20" is all of them.
"""
import argparse
import datetime as dt
from collections import defaultdict

from bot.backtest import RTH_CLOSE, RTH_OPEN, SymbolData, wilder_atr
from bot.saty_desk import ROOT
from bot.saty_options import SYMBOLS

OR_END = dt.time(9, 35)


def path(b):
    return (b.o, b.l, b.h, b.c) if b.c >= b.o else (b.o, b.h, b.l, b.c)


def day_trade(bars, side, level, stop_dist, step):
    """Walk bars after the opening range. Returns (entry, exit, entry_time, exit_time, reason) or None."""
    entry = stop = None
    for t, b in bars:
        pts = path(b)
        for a, z in zip(pts, pts[1:]):
            if entry is None:
                hit = max(a, z) >= level if side > 0 else min(a, z) <= level
                if not hit:
                    continue
                entry = max(level, a) if side > 0 else min(level, a)
                stop, et = entry - side * stop_dist, t
                a = entry                                       # the rest of this segment runs from the fill
            if (side > 0 and min(a, z) <= stop) or (side < 0 and max(a, z) >= stop):
                px = min(stop, a) if side > 0 else max(stop, a)
                return entry, px, et, t, "stop"
    if entry is None:
        return None
    return entry, bars[-1][1].c, et, bars[-1][0] + step, "close"


def candidates(data, day, o):
    out = []
    for sym, d in data.items():
        rth = d.rth(day)
        if not rth or rth[0][0].time() != RTH_OPEN:
            continue
        prior_days = [x for x in sorted(d.m5) if x < day][-14:]
        prior = [r for r in d.daily if r[0] < day]
        if len(prior_days) < 14 or len(prior) < 15:
            continue
        atr = wilder_atr(prior)
        vol14 = sum(sum(b.v for _, b in d.rth(x)) for x in prior_days) / 14
        or_hist = [d.rth(x)[0][1].v for x in prior_days if d.rth(x)]
        orb = rth[0][1]
        rel = orb.v / (sum(or_hist) / len(or_hist)) if or_hist and sum(or_hist) else 0
        if orb.o <= 5 or vol14 < 1e6 or atr <= 0.5 or orb.c == orb.o:
            continue
        if o.relvol and rel < 1.0:
            continue
        side = 1 if orb.c > orb.o else -1
        if o.small and side < 0:
            continue
        m1 = [(t, b) for t, b, _ in d.m1.get(day, []) if OR_END <= t.time() < RTH_CLOSE]
        if len(m1) >= 300 and sum(b.v for _, b in m1) > 0:
            bars, step = m1, dt.timedelta(minutes=1)
        else:
            bars, step = rth[1:], dt.timedelta(minutes=5)
        out.append({"symbol": sym, "rel": rel, "side": side, "level": orb.h if side > 0 else orb.l,
                    "atr": atr, "bars": bars, "step": step})
    out.sort(key=lambda c: -c["rel"])
    return out[:o.top]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 3, 9))
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--no-relvol", dest="relvol", action="store_false")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--stop-atr", type=float, default=0.10)
    ap.add_argument("--risk", type=float, default=0.01, help="share of capital lost at the stop")
    ap.add_argument("--leverage", type=float, default=4.0)
    ap.add_argument("--capital", type=float, default=25000.0)
    ap.add_argument("--commission", type=float, default=0.0035, help="dollars a share, each side")
    ap.add_argument("--slip", type=float, default=0.0, help="dollars a share, each fill")
    ap.add_argument("--small", action="store_true",
                    help="$1,000 cash account: long only, no leverage, the top stock only, whole shares")
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    if o.small:
        o.capital, o.leverage, o.top = 1000.0, 1.0, 1
    data = {s: SymbolData(str(ROOT / "data"), s) for s in o.symbols.split(",")}
    days = sorted({d for x in data.values() for d in x.m5 if d >= o.start and (not o.end or d <= o.end)})
    equity, trades, daily = o.capital, [], []
    for day in days:
        cands = candidates(data, day, o)
        if not cands:
            daily.append((day, 0.0))
            continue
        start_eq, pnl_day = equity, 0.0
        cap_each = o.leverage * start_eq / len(cands)
        for c in cands:
            dist = o.stop_atr * c["atr"]
            r = day_trade(c["bars"], c["side"], c["level"], dist, c["step"])
            if r is None:
                continue
            entry, exit_, et, xt, why = r
            shares = int(min(o.risk * start_eq / dist, cap_each / entry))
            if shares < 1:
                continue
            gross = c["side"] * (exit_ - entry) * shares
            cost = (2 * o.commission + 2 * o.slip) * shares
            pnl = gross - cost
            pnl_day += pnl
            trades.append({"date": day.isoformat(), "symbol": c["symbol"], "side": "long" if c["side"] > 0 else "short",
                           "relvol": round(c["rel"], 2), "entry_time": et.strftime("%H:%M"), "exit_time": xt.strftime("%H:%M"),
                           "entry": round(entry, 2), "exit": round(exit_, 2), "shares": shares, "reason": why,
                           "r_gross": round(c["side"] * (exit_ - entry) / dist, 3),
                           "r_net": round(pnl / (dist * shares), 3), "pnl_usd": round(pnl, 2)})
        equity += pnl_day
        daily.append((day, pnl_day / start_eq))
    report(trades, daily, o, equity)
    if o.trades and trades:
        import csv
        with open(o.trades, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)


def report(trades, daily, o, equity):
    n = len(trades)
    if not n:
        print("no trades")
        return
    rn = [t["r_net"] for t in trades]
    rets = [r for _, r in daily]
    m = sum(rets) / len(rets)
    sd = (sum((x - m) ** 2 for x in rets) / max(1, len(rets) - 1)) ** 0.5
    peak, eq, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    print(f"{'relvol>=1, ' if o.relvol else 'no relvol filter, '}top {o.top}, stop {o.stop_atr:.0%} ATR, "
          f"${o.capital:,.0f}{' long-only cash' if o.small else ''}: {n} trades on {sum(1 for _, r in daily if r)} days, "
          f"win {sum(r > 0 for r in rn) / n:.0%}, avg {sum(rn) / n:+.3f}R net ({sum(t['r_gross'] for t in trades) / n:+.3f}R gross), "
          f"total {sum(rn):+.0f}R")
    print(f"  account ${o.capital:,.0f} -> ${equity:,.0f} ({equity / o.capital - 1:+.1%}), "
          f"Sharpe {m / sd * 252 ** 0.5 if sd else 0:.2f}, max drawdown {dd:.1%}, worst day {min(rets):.2%}")
    by = defaultdict(list)
    for t in trades:
        by[t["date"][:7]].append(t["r_net"])
    print("  by month (R): " + ", ".join(f"{k} {sum(v):+.0f}" for k, v in sorted(by.items())))
    for side in ("long", "short"):
        s = [t["r_net"] for t in trades if t["side"] == side]
        if s:
            print(f"  {side}: {len(s)} trades, win {sum(r > 0 for r in s) / len(s):.0%}, avg {sum(s) / len(s):+.3f}R")
    sym = defaultdict(list)
    for t in trades:
        sym[t["symbol"]].append(t["r_net"])
    print("  by ticker (R): " + ", ".join(f"{k} {sum(v):+.0f}" for k, v in sorted(sym.items(), key=lambda kv: -sum(kv[1]))))


if __name__ == "__main__":
    main()
