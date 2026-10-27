"""Golden Gate with Saty's Time Warp: 3-minute candles, 10-minute ribbon (extended hours on).

    python3 -m bot.timewarp                         # every symbol with 1m data, all its days
    python3 -m bot.timewarp --set sg_exit_after_t1=false --trades out.csv

The 10m ribbon (8/13/21/34/48 EMA) is built from the 5m bars of every session, pre and post
market included, as Saty runs his charts with extended hours on. Entries are decided on 3m
candles built from the 1m regular-session bars, and fills are simulated on the 1m bars.
Rules are the SATYG ones (see bot/strategy.py), with the same Params fields; only days with
real 1m data are traded (Robinhood keeps about six weeks of 1m history).
"""
import argparse
import datetime as dt
from collections import defaultdict
from pathlib import Path

from bot.backtest import (ET, RTH_CLOSE, RTH_OPEN, SymbolData, breakdown, fmt_summary, make_params, record,
                          summarize, wilder_atr, write_trades)
from bot.strategy import ATR, EMA, ArmedEntry, Bar, DayContext, SymbolStrategy, check_price, exit_at, saty_levels


def bars_by_period(rows, minutes):
    """Group (ts, Bar) rows into bars of `minutes` aligned to the clock (9:30 for 3m from the open)."""
    groups = defaultdict(list)
    for t, b in rows:
        if minutes == 3:
            offset = (t.hour * 60 + t.minute - (9 * 60 + 30)) // 3
            key = (t.date(), offset)
        else:
            key = (t.date(), (t.hour * 60 + t.minute) // minutes)
        groups[key].append((t, b))
    out = []
    for key in sorted(groups):
        g = groups[key]
        t0 = g[0][0]
        out.append((t0, g[-1][0] + dt.timedelta(minutes=1 if minutes == 3 else 5),
                    Bar(t0, g[0][1].o, max(b.h for _, b in g), min(b.l for _, b in g), g[-1][1].c, sum(b.v for _, b in g))))
    return out


def run(data_dir, symbols, p, start=None, end=None):
    trades = []
    for sym in symbols:
        d = SymbolData(data_dir, sym)
        m1_days = [day for day, rows in d.m1.items() if any(b.v > 0 for _, b, _ in rows)]
        if not m1_days:
            continue
        # 10m ribbon over every 5m bar (all sessions), keyed by its close time
        e10 = {n: EMA(n) for n in (8, 13, 21, 34, 48)}
        ribbon = []      # (close_time, {ema: value}, 10m close)
        all5 = [(t, b) for day in sorted(d.m5) for t, b, _ in d.m5[day]]
        for t0, close_t, b10 in bars_by_period(all5, 10):
            ribbon.append((close_t, {n: e.update(b10.c) for n, e in e10.items()}, b10.c))
        strat = SymbolStrategy(sym, p)
        atr3 = ATR(14)
        ri = 0
        for day in sorted(m1_days):
            if (start and day < start) or (end and day > end):
                continue
            prior = [r for r in d.daily if r[0] < day]
            if len(prior) < 20:
                continue
            ctx = DayContext(pdc=prior[-1][4], daily_atr=wilder_atr(prior))
            strat.start_day(ctx)
            lv = saty_levels(ctx.pdc, ctx.daily_atr)
            m1 = [(t, b) for t, b, _ in d.m1[day] if RTH_OPEN <= t.time() < RTH_CLOSE]
            by_slot = defaultdict(list)
            for t, b in m1:
                by_slot[(t.hour * 60 + t.minute - 570) // 3].append(b)
            hod = lod = None
            e = None
            for t0, close_t, b3 in bars_by_period(m1, 3):
                # fills inside this 3m bar
                for fb in by_slot[(t0.hour * 60 + t0.minute - 570) // 3]:
                    for kind, payload in check_price(strat, fb, p):
                        if kind == "closed":
                            trades.append(record(day, payload))
                hod = b3.h if hod is None else max(hod, b3.h)
                lod = b3.l if lod is None else min(lod, b3.l)
                a3 = atr3.update(b3)
                strat.idx += 1
                # latest completed 10m ribbon at this 3m close
                new10 = None
                while ri < len(ribbon) and ribbon[ri][0] <= close_t:
                    e, new10 = ribbon[ri][1], ribbon[ri][2]
                    ri += 1
                if e is None:
                    continue
                pos = strat.position
                if new10 is not None and pos is not None and new10 < e[p.sg_exit_ema] and (pos.trimmed or not p.sg_exit_after_t1):
                    trades.append(record(day, exit_at(strat, b3.c, t0, f"10m close below the {p.sg_exit_ema} EMA", p)))
                    pos = None
                if strat.armed is not None and strat.idx > strat.armed.expires_after:
                    strat.armed = None
                ct = close_t.astimezone(ET).time()
                if ct >= p.flatten:
                    strat.armed = None
                    if strat.position:
                        trades.append(record(day, exit_at(strat, b3.c, t0, "flatten", p)))
                    continue
                if (strat.armed is not None or strat.position is not None or strat.trades_today >= p.max_trades_per_symbol
                        or not (p.entry_start <= ct < p.last_entry)):
                    continue
                gate, t1 = lv[0.382], lv[p.sg_t1]
                t2 = lv[p.sg_t2] if p.sg_t2 else None
                if hod < gate or b3.c <= lv[0.236] or b3.c >= t1:
                    continue
                if p.sg_above_pdc and b3.c <= ctx.pdc:
                    continue
                if not (e[8] > e[13] > e[21] > e[34] > e[48]):
                    continue
                if p.sg_max_range is not None and (hod - lod) >= p.sg_max_range * ctx.daily_atr:
                    continue
                tol = 0.1 * a3
                if not (b3.l <= e[p.sg_pullback_ema] + tol and b3.l > e[48] - tol and b3.c > e[p.sg_hold_ema]):
                    continue
                entry = round(b3.h + 0.01, 2)
                ref = lv[0.236] if p.sg_stop == "trigger" else e[int(p.sg_stop)]
                stop = round(min(b3.l, ref) - max(0.01, p.saty_stop_atr * a3), 2)
                risk = entry - stop
                if risk <= 0 or risk < p.min_risk_pct * entry or t1 - entry < p.sg_min_room_r * risk:
                    continue
                strat.armed = ArmedEntry(sym, "SATYG", "golden_gate_3m", gate, entry, stop,
                                         expires_after=strat.idx + p.arm_bars, armed_at=t0, target=t1, target2=t2)
            if strat.position and m1:
                trades.append(record(day, exit_at(strat, m1[-1][1].c, m1[-1][0], "end of data", p)))
            strat.armed = None
    trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    return trades


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--symbols")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--set", action="append", default=[], metavar="PARAM=VALUE")
    ap.add_argument("--trades")
    args = ap.parse_args(argv)
    symbols = args.symbols.split(",") if args.symbols else sorted(
        p.name.split("_")[0] for p in Path(args.data).glob("*_1m.csv"))
    params = make_params("SATYG", args.set)
    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None
    trades = run(args.data, symbols, params, start, end)
    print(fmt_summary(summarize(trades)))
    for name, key in (("symbol", lambda t: t["symbol"]), ("exit", lambda t: t["exit_reason"]),
                      ("month", lambda t: t["date"][:7])):
        print(f"\nby {name}:")
        for k, s in breakdown(trades, key).items():
            print(f"  {k:<28} {fmt_summary(s)}")
    if args.trades:
        write_trades(trades, Path(args.trades))


if __name__ == "__main__":
    main()
