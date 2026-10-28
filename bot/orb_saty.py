"""15-minute opening range breakout combined with Saty's ATR levels and Pivot Ribbon.

    python3 -m bot.orb_saty --grid                  # every combination below, build Mar-Jul, check Aug, Sep-Oct
    python3 -m bot.orb_saty --entry trigger --confirm fast --stop level --exit r13

The 15-minute opening range (9:30-9:45 ET) sets the side as in Zarattini, Barbon & Aziz (2024):
green range -> longs only, red -> shorts only (--direction both: whichever side breaks).
--entry  orb      buy-stop at the range high (sell-stop at the low)
         trigger  the same, but the price must also clear Saty's call trigger (+23.6% ATR; put
                  trigger for shorts): entry at the farther of the two
         level    buy-stop at the first ATR level (23.6, 38.2, 50, 61.8, 78.6, 100%) beyond the range
--confirm (10-minute Pivot Ribbon with extended hours, last 10m close before the fill)
         none | fast: 8 EMA over 21 | cloud: 10m close over the 34 and 48 | stack: 8>13>21>34>48
--stop   atr10: 10% of ATR(14) from the fill (the paper) | or_mid: middle of the range |
         level: the ATR level below the entry
--exit   eod: hold to 16:00 | r13: a 10m close under the 13 EMA (Saty's hold rule) |
         r48: a 10m close under the 48 (trend over) | targets: a third at each of the next two ATR
         levels, the rest until a 10m close under the 21 or 16:00
--relvol volume of the opening range >= its 14-day average (stocks in play)
One trade per stock per day, entries until 12:00 by default. Results in R after --slip dollars a
share per fill. Shorts run the long logic on negated prices.
"""
import argparse
import bisect
import datetime as dt
import itertools
from collections import defaultdict
from multiprocessing import Pool

from bot.backtest import RTH_CLOSE, RTH_OPEN, SymbolData, wilder_atr
from bot.saty_desk import ROOT, ribbon_series
from bot.saty_options import SYMBOLS
from bot.strategy import Bar, SATY_FIBS, saty_levels

PERIODS = {"build": (dt.date(2026, 3, 9), dt.date(2026, 7, 31)), "check": (dt.date(2026, 8, 1), dt.date(2026, 8, 31)),
           "holdout": (dt.date(2026, 9, 1), dt.date(2026, 10, 27))}
GRID = {"entry": ["orb", "trigger", "level"], "confirm": ["none", "fast", "cloud", "stack"],
        "stop": ["atr10", "or_mid", "level"], "exit": ["eod", "r13", "r48", "targets"], "relvol": [False, True]}


def neg(b):
    return Bar(b.ts, -b.o, -b.l, -b.h, -b.c, b.v)


def path(b):
    return (b.o, b.l, b.h, b.c) if b.c >= b.o else (b.o, b.h, b.l, b.c)


def setups(sym, or_minutes=15, use_1m=True):
    """Per day: everything the rules need, prepared once. Prices for shorts are negated."""
    d = SymbolData(str(ROOT / "data"), sym)
    rib = ribbon_series(d)
    rib_t = [x[0] for x in rib]
    days = sorted(d.m5)
    out = []
    for i, day in enumerate(days):
        rth = d.rth(day)
        prior = [r for r in d.daily if r[0] < day]
        if i < 14 or len(prior) < 15 or not rth or rth[0][0].time() != RTH_OPEN:
            continue
        n_or = or_minutes // 5
        orb = rth[:n_or]
        o, h, l, c = orb[0][1].o, max(b.h for _, b in orb), min(b.l for _, b in orb), orb[-1][1].c
        or_vol = sum(b.v for _, b in orb)
        hist = [sum(b.v for _, b in d.rth(x)[:n_or]) for x in days[i - 14:i]]
        vol14 = sum(sum(b.v for _, b in d.rth(x)) for x in days[i - 14:i]) / 14
        atr, pdc = wilder_atr(prior), prior[-1][4]
        if o <= 5 or vol14 < 1e6 or atr <= 0.5:
            continue
        or_end = orb[-1][0] + dt.timedelta(minutes=5)
        m1 = [(t, b) for t, b, _ in d.m1.get(day, []) if or_end.time() <= t.time() < RTH_CLOSE]
        if use_1m and len(m1) >= 300 and sum(b.v for _, b in m1) > 0:
            bars, step = m1, 1
        else:
            bars, step = rth[n_or:], 5
        j0 = bisect.bisect_left(rib_t, or_end - dt.timedelta(hours=1))
        j1 = bisect.bisect_right(rib_t, dt.datetime.combine(day, RTH_CLOSE, tzinfo=or_end.tzinfo))
        ribbon = [(t, v, b10.c) for t, v, b10 in rib[j0:j1]]
        lv = saty_levels(pdc, atr)
        for side in (1, -1):
            s = side
            out.append({
                "symbol": sym, "date": day, "side": side, "or_dir": 1 if c > o else -1 if c < o else 0,
                "relvol": or_vol / (sum(hist) / 14) if sum(hist) else 0, "atr": atr,
                "or_hi": s * h if s > 0 else -l, "or_lo": s * l if s > 0 else -h,
                "levels": sorted(s * lv[f * s] for f in SATY_FIBS[:6]), "trigger": s * lv[0.236 * s],
                "bars": [(t, b if s > 0 else neg(b)) for t, b in bars], "step": dt.timedelta(minutes=step),
                "ribbon": [(t, {k: s * v[k] for k in (8, 13, 21, 34, 48)}, s * cl) for t, v, cl in ribbon],
            })
    return out


def confirmed(mode, rb):
    if mode == "none":
        return True
    if rb is None:
        return False
    v, c = rb
    if mode == "fast":
        return v[8] > v[21]
    if mode == "cloud":
        return c > v[34] and c > v[48]
    if mode == "stack":
        return v[8] > v[13] > v[21] > v[34] > v[48]
    raise ValueError(mode)


def simulate(s, p):
    """One stock-day-side under parameters p. Returns a trade dict or None."""
    if p["direction"] == "candle" and s["or_dir"] != s["side"]:
        return None
    if p["relvol"] and s["relvol"] < 1.0:
        return None
    if p["entry"] == "orb":
        level = s["or_hi"]
    elif p["entry"] == "trigger":
        level = max(s["or_hi"], s["trigger"])
    else:
        above = [x for x in s["levels"] if x > s["or_hi"]]
        if not above:
            return None
        level = above[0]
    rib = s["ribbon"]
    rib_t = [t for t, _, _ in rib]
    last_entry = dt.time(*map(int, p["last"].split(":")))
    entry = stop = None
    fills, left, targets = [], 1.0, []
    for t, b in s["bars"]:
        end = t + s["step"]
        pts = path(b)
        for a, z in zip(pts, pts[1:]):
            if entry is None:
                if max(a, z) < level or t.time() >= last_entry:
                    continue
                k = bisect.bisect_right(rib_t, t) - 1
                rb = (rib[k][1], rib[k][2]) if k >= 0 else None
                if not confirmed(p["confirm"], rb):
                    return None                                 # first break unconfirmed: no trade today
                entry = max(level, a)
                if p["stop"] == "atr10":
                    stop = entry - 0.1 * s["atr"]
                elif p["stop"] == "or_mid":
                    stop = (s["or_hi"] + s["or_lo"]) / 2
                else:
                    below = [x for x in s["levels"] if x < entry - 0.02 * s["atr"]]
                    stop = below[-1] if below else s["or_lo"]
                stop = min(stop, entry - 0.05 * s["atr"])       # never tighter than 5% ATR
                if p["exit"] == "targets":
                    targets = [x for x in s["levels"] if x > entry + 0.05 * s["atr"]][:2]
                opened, a = t, entry
            if min(a, z) <= stop:
                fills.append((min(stop, a), left, "stop"))
                return finish(s, entry, stop, fills, opened, t)
            while targets and max(a, z) >= targets[0]:
                fills.append((max(targets.pop(0), a), 1 / 3, "target"))
                left -= 1 / 3
        if entry is not None and p["exit"] in ("r13", "r48", "targets") and end.minute % 10 == 0:
            k = bisect.bisect_right(rib_t, end) - 1
            if k >= 0 and rib_t[k] == end:
                ema = {"r13": 13, "r48": 48, "targets": 21}[p["exit"]]
                if rib[k][2] < rib[k][1][ema]:
                    fills.append((b.c, left, f"10m close under {ema}"))
                    return finish(s, entry, stop, fills, opened, end)
    if entry is None:
        return None
    fills.append((s["bars"][-1][1].c, left, "close"))
    return finish(s, entry, stop, fills, opened, s["bars"][-1][0])


def finish(s, entry, stop, fills, opened, closed):
    risk = entry - stop
    r = sum(q * (px - entry) for px, q, _ in fills) / risk
    return {"symbol": s["symbol"], "date": s["date"], "side": s["side"], "entry": s["side"] * entry,
            "risk": risk, "r": r, "reason": fills[-1][2], "opened": opened, "closed": closed}


def evaluate(args):
    p, slip = args
    res = defaultdict(list)
    for s in SETUPS:
        t = simulate(s, p)
        if t is None:
            continue
        r = t["r"] - 2 * slip / t["risk"]
        for name, (a, b) in PERIODS.items():
            if a <= t["date"] <= b:
                res[name].append(r)
    return p, {k: (len(v), sum(v)) for k, v in res.items()}


SETUPS = []


def load(symbols, or_minutes, use_1m):
    global SETUPS
    with Pool() as pool:
        SETUPS = [x for xs in pool.starmap(setups, [(s, or_minutes, use_1m) for s in symbols]) for x in xs]


def line(p, r):
    def cell(k):
        n, tot = r.get(k, (0, 0.0))
        return f"{n:4d} {tot / n if n else 0:+.3f}R {tot:+6.0f}R"
    return " | ".join(cell(k) for k in PERIODS) + "  " + " ".join(f"{k}={v}" for k, v in p.items() if k in GRID or k == "direction")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--or-minutes", type=int, default=15)
    ap.add_argument("--direction", choices=["candle", "both"], default="candle")
    ap.add_argument("--entry", choices=GRID["entry"], default="orb")
    ap.add_argument("--confirm", choices=GRID["confirm"], default="none")
    ap.add_argument("--stop", choices=GRID["stop"], default="atr10")
    ap.add_argument("--exit", choices=GRID["exit"], default="eod")
    ap.add_argument("--relvol", action="store_true")
    ap.add_argument("--last", default="12:00", help="no new entries from this time")
    ap.add_argument("--slip", type=float, default=0.01, help="dollars a share, each fill")
    ap.add_argument("--no-1m", dest="use_1m", action="store_false", help="5-minute bars everywhere")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    o = ap.parse_args(argv)
    load(o.symbols.split(","), o.or_minutes, o.use_1m)
    base = {"direction": o.direction, "last": o.last}
    print(f"{len(SETUPS) // 2} stock-days; columns: " + " | ".join(f"{k} {a:%b %d}-{b:%b %d} (n avg total)" for k, (a, b) in PERIODS.items()))
    if not o.grid:
        p, r = evaluate(({**base, "entry": o.entry, "confirm": o.confirm, "stop": o.stop, "exit": o.exit,
                          "relvol": o.relvol}, o.slip))
        print(line(p, r))
        return
    combos = [dict(base, **dict(zip(GRID, v))) for v in itertools.product(*GRID.values())]
    with Pool() as pool:
        results = pool.map(evaluate, [(p, o.slip) for p in combos])
    ok = [(p, r) for p, r in results if r.get("build", (0, 0))[0] >= 100]
    ok.sort(key=lambda pr: -pr[1]["build"][1] / pr[1]["build"][0])
    print(f"{len(combos)} combinations, {len(ok)} with 100+ build trades; best by build R/trade:")
    for p, r in ok[:o.top]:
        print(" ", line(p, r))
    pos = lambda k: sum(1 for _, r in ok if r.get(k, (0, 0))[1] > 0)
    print(f"positive: build {pos('build')}/{len(ok)}, check {pos('check')}/{len(ok)}, holdout {pos('holdout')}/{len(ok)}")
    for k in GRID:                                              # each choice's average across all combos (build)
        avg = defaultdict(list)
        for p, r in ok:
            n, tot = r["build"]
            avg[p[k]].append(tot / n)
        print(f"  {k}: " + ", ".join(f"{v} {sum(x) / len(x):+.3f}R" for v, x in avg.items()))


if __name__ == "__main__":
    main()
