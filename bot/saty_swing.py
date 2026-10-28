"""Saty multi-day swing: weekly ATR levels, daily Pivot Ribbon, held for days.

    python3 -m bot.saty_swing --end 2026-07-31
    python3 -m bot.saty_swing --start 2026-09-01 --slip 0.0006

Saty's multi-day mode puts ATR levels on the weekly ATR (levels from the previous week's close).
Here: a daily close above the previous week's close (or the weekly call trigger, --need-trigger), with the daily
8/13/21/34/48 EMA ribbon stacked bullish and a pullback into the 8-13 zone that closes back above
the 13 (and holds the 48). Buy-stop above that day's high the next day; stop under the pullback
low and the daily 21 EMA. Scale out a third at each weekly level above (at least min_reward_risk R
to the last one), stop to breakeven after the first; the rest out on a daily close below the 21,
everything on a close below the 48, or after --max-days. Daily bars only, so inside a day the
open-low-high-close order is assumed for a green day (open-high-low-close for a red one).
"""
import argparse
import datetime as dt
import json
from collections import defaultdict

from bot.backtest import breakdown, fmt_summary, summarize, wilder_atr, SymbolData
from bot.saty_desk import ROOT
from bot.strategy import EMA, saty_levels

SYMBOLS = "RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR"


def weekly_bars(daily):
    weeks = defaultdict(list)
    for r in daily:
        weeks[r[0].isocalendar()[:2]].append(r)
    out = []
    for k in sorted(weeks):
        w = weeks[k]
        out.append((w[-1][0], w[0][1], max(x[2] for x in w), min(x[3] for x in w), w[-1][4]))
    return out


def run_symbol(sym, o, min_rr=2.0):
    daily = SymbolData(str(ROOT / "data"), sym).daily
    weeks = weekly_bars(daily)
    e = {n: EMA(n) for n in (8, 13, 21, 34, 48)}
    trades, pos, armed, prev = [], None, None, None
    for i, (day, op, hi, lo, cl) in enumerate(daily):
        # fills and exits during the day
        if armed and pos is None and hi >= armed["entry"]:
            fill = max(armed["entry"], op) * (1 + o.slip)
            if fill - armed["entry"] <= 0.5 * (armed["entry"] - armed["stop"]):
                pos = dict(armed, entry=fill, risk=fill - armed["stop"], left=1.0, r=0.0, done=0, opened=day, days=0)
            armed = None
            green = cl >= op
        else:
            armed, green = None, False
        if pos:
            pos["days"] += 1
            def sell(px, frac, why):
                pos["r"] += frac * (px - pos["entry"]) / pos["risk"]
                pos["left"] -= frac
                if pos["left"] <= 1e-9:
                    pos["why"] = why
            if not (green and pos["days"] == 1) and lo <= pos["stop"]:
                sell(min(pos["stop"], op) * (1 - o.slip), pos["left"], "stop" if pos["done"] == 0 else "break-even stop")
            else:
                while pos["done"] < len(pos["targets"]) and hi >= pos["targets"][pos["done"]][1]:
                    name, px, frac = pos["targets"][pos["done"]]
                    sell(max(px, op) * (1 - o.slip), min(frac, pos["left"]), "target " + name)
                    pos["done"] += 1
                    if pos["done"] == 1:
                        pos["stop"] = max(pos["stop"], pos["entry"])
                    if pos["left"] <= 1e-9:
                        break
        v = {n: x.update(cl) for n, x in e.items()}
        if pos and pos["left"] > 1e-9:
            if cl < v[48]:
                pos["r"] += pos["left"] * (cl * (1 - o.slip) - pos["entry"]) / pos["risk"]; pos["left"] = 0; pos["why"] = "daily close below the 48"
            elif cl < v[21]:
                pos["r"] += pos["left"] * (cl * (1 - o.slip) - pos["entry"]) / pos["risk"]; pos["left"] = 0; pos["why"] = "daily close below the 21"
            elif pos["days"] >= o.max_days:
                pos["r"] += pos["left"] * (cl * (1 - o.slip) - pos["entry"]) / pos["risk"]; pos["left"] = 0; pos["why"] = "time"
        if pos and pos["left"] <= 1e-9:
            trades.append({"date": pos["opened"].isoformat(), "symbol": sym, "setup": "swing", "exit_reason": pos["why"],
                           "r": round(pos["r"], 3), "days": pos["days"], "exit_date": day.isoformat()})
            pos = None
        # entry decision at today's close (levels from last completed week)
        if pos or i < 60 or (o.start and day < o.start) or (o.end and day > o.end):
            prev = v
            continue
        past = [w for w in weeks if w[0].isocalendar()[:2] < day.isocalendar()[:2]]
        if len(past) < 20:
            prev = v
            continue
        lv = saty_levels(past[-1][4], wilder_atr(past))
        trig, wclose, full = lv[0.236], past[-1][4], lv[1.0]
        stacked = v[8] > v[13] > v[21] > v[34] > v[48]
        tol = 0.1 * (hi - lo)
        pullback = lo <= v[8] + tol and lo > v[48] and cl > v[13]
        above = cl > trig if o.need_trigger else cl > wclose
        if stacked and pullback and above and cl < full:
            entry = round(hi * 1.0005, 2)
            stop = round(min(lo, v[21]) * 0.998, 2)
            risk = entry - stop
            ups = [(f"+{f:.1%}", lv[f]) for f in (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.236) if lv[f] >= entry + o.min_target_r * risk]
            if risk > 0 and ups and max(p for _, p in ups) >= entry + min_rr * risk:
                tg = [(n, p, 1 / 3) for n, p in ups[:2]] + ([("+100.0%", full, 1 / 3)] if full > ups[min(1, len(ups) - 1)][1] else [])
                tg[-1] = (tg[-1][0], tg[-1][1], 1.0)
                armed = {"entry": entry, "stop": stop, "targets": tg}
        prev = v
    return trades


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--slip", type=float, default=0.0003)
    ap.add_argument("--max-days", type=int, default=10)
    ap.add_argument("--need-trigger", action="store_true", help="require a close above the weekly call trigger")
    ap.add_argument("--min-target-r", type=float, default=1.0, help="scale-out levels at least this many R up")
    o = ap.parse_args(argv)
    cfg = json.loads((ROOT / "desk" / "config.json").read_text())
    trades = []
    for s in o.symbols.split(","):
        trades += run_symbol(s, o, cfg["risk"]["min_reward_risk"])
    trades.sort(key=lambda t: t["date"])
    print(fmt_summary(summarize(trades)))
    for name, key in (("exit", lambda t: t["exit_reason"]), ("symbol", lambda t: t["symbol"])):
        print(f"\nby {name}:")
        for k, s in breakdown(trades, key).items():
            print(f"  {k:<26} {fmt_summary(s)}")
    return trades


if __name__ == "__main__":
    main()
