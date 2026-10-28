"""15-minute opening range breakout combined with Saty's ATR levels and Pivot Ribbon.

    python3 -m bot.orb_saty --grid                  # every combination below, build Mar-Jul, check Aug, Sep-Oct
    python3 -m bot.orb_saty --entry trigger --confirm fast --stop level --exit r13
    python3 -m bot.orb_saty --grid5                 # 15m ORB, 5m ribbon hold + ATR-level take profits

The 15-minute opening range (9:30-9:45 ET) sets the side as in Zarattini, Barbon & Aziz (2024):
green range -> longs only, red -> shorts only (--direction both: whichever side breaks).
--entry  orb      buy-stop at the range high (sell-stop at the low)
         trigger  the same, but the price must also clear Saty's call trigger (+23.6% ATR; put
                  trigger for shorts): entry at the farther of the two
         level    buy-stop at the first ATR level (23.6, 38.2, 50, 61.8, 78.6, 100%) beyond the range
--confirm (10-minute Pivot Ribbon with extended hours, last 10m close before the fill)
         none | fast: 8 EMA over 21 | cloud: 10m close over the 34 and 48 | stack: 8>13>21>34>48
         (5-minute ribbon, extended hours) hold13: the last 3 five-minute closes at or over the 5m
         13 EMA | hold13_fast: that and the 5m 8 EMA over the 21
--rebreak  if the first break isn't confirmed, wait for the price to close back under the level
         and take a later confirmed break (default: no trade that day)
--stop   atr10: 10% of ATR(14) from the fill (the paper) | or_mid: middle of the range |
         level: the ATR level below the entry | ema21: the 5m 21 EMA at the fill
--exit   eod: hold to 16:00 | r13: a 10m close under the 13 EMA (Saty's hold rule) |
         r48: a 10m close under the 48 (trend over) | targets: a third at each of the next two ATR
         levels, the rest until a 10m close under the 21 or 16:00
         (5-minute ribbon) c13 / c21: a 5m close under the 13 / 21 EMA | lv_c13: a third at each of
         the next two ATR levels, the rest until a 5m close under the 13 | lv1_c13: half at the
         next ATR level, the rest on the 13 | lv_eod: thirds at the next two levels, the rest to 16:00
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
from bot.strategy import EMA, Bar, SATY_FIBS, saty_levels

PERIODS = {"build": (dt.date(2026, 3, 9), dt.date(2026, 7, 31)), "check": (dt.date(2026, 8, 1), dt.date(2026, 8, 31)),
           "holdout": (dt.date(2026, 9, 1), dt.date(2026, 10, 27))}
GRID = {"entry": ["orb", "trigger", "level"], "confirm": ["none", "fast", "cloud", "stack"],
        "stop": ["atr10", "or_mid", "level"], "exit": ["eod", "r13", "r48", "targets"], "relvol": [False, True]}
GRID5 = {"direction": ["candle", "both"], "entry": ["orb", "trigger"], "confirm": ["none", "hold13", "hold13_fast"],
         "rebreak": [False, True], "stop": ["atr10", "or_mid", "level", "ema21"],
         "exit": ["eod", "c13", "c21", "lv_c13", "lv1_c13", "lv_eod"], "relvol": [False, True]}
TARGETS = {"targets": 2, "lv_c13": 2, "lv1_c13": 1, "lv_eod": 2}     # ATR-level take profits before the hold rule
HOLD = {"r13": (10, 13), "r48": (10, 48), "targets": (10, 21), "c13": (5, 13), "c21": (5, 21), "lv_c13": (5, 13),
        "lv1_c13": (5, 13)}                                               # (ribbon minutes, EMA) a close under it exits


def neg(b):
    return Bar(b.ts, -b.o, -b.l, -b.h, -b.c, b.v)


def path(b):
    return (b.o, b.l, b.h, b.c) if b.c >= b.o else (b.o, b.h, b.l, b.c)


def ribbon5_series(d):
    """[(close_time, {ema: value}, close)] on every 5-minute bar, all sessions, carried across days."""
    e = {n: EMA(n) for n in (8, 13, 21, 34, 48)}
    return [(t + dt.timedelta(minutes=5), {n: x.update(b.c) for n, x in e.items()}, b.c)
            for day in sorted(d.m5) for t, b, _ in d.m5[day]]


def day_slice(series, times, start, day, tz):
    j0 = bisect.bisect_left(times, start)
    j1 = bisect.bisect_right(times, dt.datetime.combine(day, RTH_CLOSE, tzinfo=tz))
    return series[j0:j1]


def setups(sym, or_minutes=15, use_1m=True):
    """Per day: everything the rules need, prepared once. Prices for shorts are negated."""
    d = SymbolData(str(ROOT / "data"), sym)
    rib = ribbon_series(d)
    rib_t = [x[0] for x in rib]
    rib5 = ribbon5_series(d)
    rib5_t = [x[0] for x in rib5]
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
        ribbon = [(t, v, b10.c) for t, v, b10 in day_slice(rib, rib_t, or_end - dt.timedelta(hours=1), day, or_end.tzinfo)]
        ribbon5 = day_slice(rib5, rib5_t, or_end - dt.timedelta(hours=1), day, or_end.tzinfo)
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
                "ribbon5": [(t, {k: s * v[k] for k in (8, 13, 21, 34, 48)}, s * cl) for t, v, cl in ribbon5],
            })
    return out


def confirmed(mode, rb, last5=()):
    if mode == "none":
        return True
    if mode in ("hold13", "hold13_fast"):
        if len(last5) < 3 or any(c < v[13] for _, v, c in last5[-3:]):
            return False
        return mode == "hold13" or last5[-1][1][8] > last5[-1][1][21]
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
    rib, rib5 = s["ribbon"], s.get("ribbon5", [])
    rib_t, rib5_t = [t for t, _, _ in rib], [t for t, _, _ in rib5]
    last_entry = dt.time(*map(int, p["last"].split(":")))
    entry = stop = None
    armed = True
    n_tp = TARGETS.get(p["exit"], 0)
    hold = HOLD.get(p["exit"])
    fills, left, targets = [], 1.0, []
    for t, b in s["bars"]:
        end = t + s["step"]
        pts = path(b)
        for a, z in zip(pts, pts[1:]):
            if entry is None:
                if not armed or max(a, z) < level or t.time() >= last_entry:
                    continue
                k = bisect.bisect_right(rib_t, t) - 1
                rb = (rib[k][1], rib[k][2]) if k >= 0 else None
                k5 = bisect.bisect_right(rib5_t, t)
                if not confirmed(p["confirm"], rb, rib5[max(0, k5 - 3):k5]):
                    if not p.get("rebreak"):
                        return None                             # first break unconfirmed: no trade today
                    armed = False                               # wait for a close back under the level
                    break
                entry = max(level, a)
                if p["stop"] == "atr10":
                    stop = entry - 0.1 * s["atr"]
                elif p["stop"] == "or_mid":
                    stop = (s["or_hi"] + s["or_lo"]) / 2
                elif p["stop"] == "ema21":
                    stop = rib5[k5 - 1][1][21] if k5 else s["or_lo"]
                else:
                    below = [x for x in s["levels"] if x < entry - 0.02 * s["atr"]]
                    stop = below[-1] if below else s["or_lo"]
                stop = min(stop, entry - 0.05 * s["atr"])       # never tighter than 5% ATR
                targets = [x for x in s["levels"] if x > entry + 0.05 * s["atr"]][:n_tp]
                opened, a = t, entry
            if min(a, z) <= stop:
                fills.append((min(stop, a), left, "stop"))
                return finish(s, entry, stop, fills, opened, t)
            while targets and max(a, z) >= targets[0]:
                fills.append((max(targets.pop(0), a), 1 / (n_tp + 1), "target"))
                left -= 1 / (n_tp + 1)
        if entry is None:
            if not armed and b.c < level:
                armed = True
            continue
        if hold and end.minute % hold[0] == 0:
            series, times = (rib, rib_t) if hold[0] == 10 else (rib5, rib5_t)
            k = bisect.bisect_right(times, end) - 1
            if k >= 0 and times[k] == end and series[k][2] < series[k][1][hold[1]]:
                fills.append((b.c, left, f"{hold[0]}m close under {hold[1]}"))
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
    return " | ".join(cell(k) for k in PERIODS) + "  " + " ".join(f"{k}={v}" for k, v in p.items() if k in GRID or k in GRID5)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--or-minutes", type=int, default=15)
    ap.add_argument("--direction", choices=["candle", "both"], default="candle")
    ap.add_argument("--entry", choices=GRID["entry"], default="orb")
    ap.add_argument("--confirm", choices=GRID["confirm"] + GRID5["confirm"][1:], default="none")
    ap.add_argument("--rebreak", action="store_true")
    ap.add_argument("--stop", choices=GRID5["stop"], default="atr10")
    ap.add_argument("--exit", choices=GRID["exit"] + GRID5["exit"][1:], default="eod")
    ap.add_argument("--relvol", action="store_true")
    ap.add_argument("--last", default="12:00", help="no new entries from this time")
    ap.add_argument("--slip", type=float, default=0.01, help="dollars a share, each fill")
    ap.add_argument("--no-1m", dest="use_1m", action="store_false", help="5-minute bars everywhere")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--grid5", action="store_true", help="the 5-minute ribbon grid (GRID5)")
    ap.add_argument("--top", type=int, default=15)
    o = ap.parse_args(argv)
    load(o.symbols.split(","), o.or_minutes, o.use_1m)
    base = {"direction": o.direction, "last": o.last}
    print(f"{len(SETUPS) // 2} stock-days; columns: " + " | ".join(f"{k} {a:%b %d}-{b:%b %d} (n avg total)" for k, (a, b) in PERIODS.items()))
    if not (o.grid or o.grid5):
        p, r = evaluate(({**base, "entry": o.entry, "confirm": o.confirm, "rebreak": o.rebreak, "stop": o.stop,
                          "exit": o.exit, "relvol": o.relvol}, o.slip))
        print(line(p, r))
        return
    grid = GRID5 if o.grid5 else GRID
    combos = [dict(base, **dict(zip(grid, v))) for v in itertools.product(*grid.values())]
    with Pool() as pool:
        results = pool.map(evaluate, [(p, o.slip) for p in combos])
    ok = [(p, r) for p, r in results if r.get("build", (0, 0))[0] >= 100]
    ok.sort(key=lambda pr: -pr[1]["build"][1] / pr[1]["build"][0])
    print(f"{len(combos)} combinations, {len(ok)} with 100+ build trades; best by build R/trade:")
    for p, r in ok[:o.top]:
        print(" ", line(p, r))
    pos = lambda k: sum(1 for _, r in ok if r.get(k, (0, 0))[1] > 0)
    print(f"positive: build {pos('build')}/{len(ok)}, check {pos('check')}/{len(ok)}, holdout {pos('holdout')}/{len(ok)}")
    for k in grid:                                              # each choice's average across all combos (build)
        avg = defaultdict(list)
        for p, r in ok:
            n, tot = r["build"]
            avg[p[k]].append(tot / n)
        print(f"  {k}: " + ", ".join(f"{v} {sum(x) / len(x):+.3f}R" for v, x in avg.items()))


if __name__ == "__main__":
    main()
