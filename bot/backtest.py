"""Backtest the strategy on Robinhood bars saved by bot.ingest.

    python3 -m bot.backtest                       # all symbols in data/, default params
    python3 -m bot.backtest --setups BRH,EMA --exec 1m --start 2026-09-30
    python3 -m bot.backtest --report reports/     # baseline + variants, markdown report

Execution model:
  - decisions only on completed 5-minute bars (same code as live)
  - entries, stops and first targets are checked on 1-minute bars when they exist
    for that day ("--exec 1m"), otherwise on the 5-minute bar itself
  - inside one bar the stop is assumed to hit before the target (pessimistic)
  - slippage Params.slippage_pct per fill, no commissions
Results are in R (multiples of the risk taken on each trade).
"""
import argparse
import csv
import dataclasses
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.strategy import Bar, DayContext, EMA, Params, SymbolStrategy, check_price, exit_at

ET = ZoneInfo("America/New_York")
RTH_OPEN, RTH_CLOSE = dt.time(9, 30), dt.time(16, 0)
MIN_HISTORY_SESSIONS = 10


# ---- data --------------------------------------------------------------------

def read_bars(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            out.append((ts, Bar(ts, float(r["open"]), float(r["high"]), float(r["low"]),
                                float(r["close"]), float(r["volume"])), r["session"]))
    return out


def by_session_date(rows):
    days = defaultdict(list)
    for ts, bar, session in rows:
        days[ts.astimezone(ET).date()].append((ts.astimezone(ET), bar, session))
    return days


class SymbolData:
    def __init__(self, data_dir, symbol):
        d = Path(data_dir)
        self.symbol = symbol
        self.m5 = by_session_date(read_bars(d / f"{symbol}_5m.csv"))
        m1 = d / f"{symbol}_1m.csv"
        self.m1 = by_session_date(read_bars(m1)) if m1.exists() else {}
        self.daily = []
        with open(d / f"{symbol}_1d.csv") as f:
            for r in csv.DictReader(f):
                self.daily.append((dt.date.fromisoformat(r["ts"][:10]), float(r["open"]), float(r["high"]),
                                   float(r["low"]), float(r["close"])))
        self.daily.sort()

    def rth(self, day):
        return [(t, b) for t, b, s in self.m5.get(day, []) if RTH_OPEN <= t.time() < RTH_CLOSE]

    def context(self, day, sessions):
        """Levels and baselines known before the open of `day`."""
        ctx = DayContext()
        prior = [row for row in self.daily if row[0] < day]
        if prior:
            _, _, ctx.pdh, ctx.pdl, ctx.pdc = prior[-1]
            e9, e50, e200 = EMA(9), EMA(50), EMA(200)
            for row in prior:
                e9.update(row[4])
                e50.update(row[4])
                e200.update(row[4])
            ctx.daily_ema9, ctx.daily_ema50 = e9.value, e50.value
            ctx.daily_atr = wilder_atr(prior)
            ctx.daily_ema200 = e200.value if len(prior) >= 200 else None
            ctx.high60 = max(r[2] for r in prior[-60:])
            if len(prior) > 20:
                ctx.ret20 = prior[-1][4] / prior[-21][4] - 1
            this_week = day.isocalendar()[:2]
            weeks = sorted({row[0].isocalendar()[:2] for row in prior if row[0].isocalendar()[:2] < this_week})
            if weeks:
                last = [row for row in prior if row[0].isocalendar()[:2] == weeks[-1]]
                ctx.pwh, ctx.pwl = max(r[2] for r in last), min(r[3] for r in last)
        pre = [b for t, b, s in self.m5.get(day, []) if dt.time(4, 0) <= t.time() < RTH_OPEN]
        if pre:
            ctx.pmh, ctx.pml = max(b.h for b in pre), min(b.l for b in pre)
        slots = defaultdict(list)
        for d in sessions[-MIN_HISTORY_SESSIONS:]:
            for t, b in self.rth(d):
                slots[t.strftime("%H:%M")].append(b.v)
        ctx.slot_volume = {k: sum(v) / len(v) for k, v in slots.items() if len(v) >= 5}
        return ctx


def wilder_atr(rows, period=14):
    """ATR as TradingView's ta.atr: RMA of true range, seeded with the first `period` TRs' mean."""
    trs = [rows[0][2] - rows[0][3]] + [max(h - l, abs(h - pc), abs(l - pc))
                                       for (_, _, h, l, _), (_, _, _, _, pc) in zip(rows[1:], rows)]
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr += (tr - atr) / period
    return atr


# ---- simulation ----------------------------------------------------------------

def run(data_dir, symbols, params: Params, exec_mode="1m", start=None, end=None, benchmark="SPY"):
    data = {s: SymbolData(data_dir, s) for s in set(symbols) | {benchmark}}
    days = sorted(data[benchmark].m5)
    strategies = {s: SymbolStrategy(s, params) for s in symbols}
    trades = []
    sessions_seen = []
    spy_ema = EMA(9)
    for day in days:
        trading = len(sessions_seen) >= MIN_HISTORY_SESSIONS and (not start or day >= start) and (not end or day <= end)
        spy = data[benchmark].rth(day)
        if not spy:
            continue
        spy_open = spy[0][1].o
        spy_change = {t.strftime("%H:%M"): b.c / spy_open - 1 for t, b in spy}
        market = market_state(spy, spy_ema)
        rth = {s: {t.strftime("%H:%M"): (t, b) for t, b in data[s].rth(day)} for s in symbols}
        fine = {}
        for s in symbols:
            if exec_mode == "1m" and day in data[s].m1:
                groups = defaultdict(list)
                for t, b, _ in data[s].m1[day]:
                    if RTH_OPEN <= t.time() < RTH_CLOSE:
                        slot = t.replace(minute=t.minute - t.minute % 5)
                        groups[slot.strftime("%H:%M")].append(b)
                fine[s] = groups
        spy_ctx = data[benchmark].context(day, sessions_seen)
        for s in symbols:
            ctx = data[s].context(day, sessions_seen)
            ctx.spy_ret20 = spy_ctx.ret20
            strategies[s].start_day(ctx)
        for slot in sorted({k for bars in rth.values() for k in bars}):
            for s in symbols:
                if slot not in rth[s]:
                    continue
                strat = strategies[s]
                t, bar = rth[s][slot]
                close_time = (t + dt.timedelta(minutes=5)).time()
                for fb in fine.get(s, {}).get(slot) or [bar]:
                    for kind, payload in check_price(strat, fb, params):
                        if kind == "closed":
                            trades.append(record(day, payload))
                for kind, payload in strat.on_5m_close(bar, slot, close_time, spy_change.get(slot), market.get(slot)):
                    if kind == "exit" and strat.position:
                        trades.append(record(day, exit_at(strat, bar.c, t, payload, params)))
                if not trading:
                    strat.armed = None
                if close_time >= params.flatten:
                    strat.armed = None
                    if strat.position:
                        trades.append(record(day, exit_at(strat, bar.c, t, "flatten", params)))
        for s in symbols:
            strat = strategies[s]
            if strat.position and rth[s]:
                t, bar = rth[s][max(rth[s])]
                trades.append(record(day, exit_at(strat, bar.c, t, "end of data", params)))
            strat.armed = None
        sessions_seen.append(day)
    return trades


def market_state(spy_bars, spy_ema):
    """SPY above its session VWAP / its 5m 9 EMA, per bar slot."""
    out, pv, vol = {}, 0.0, 0.0
    for t, b in spy_bars:
        pv += (b.h + b.l + b.c) / 3 * b.v
        vol += b.v
        ema = spy_ema.update(b.c)
        out[t.strftime("%H:%M")] = {"above_vwap": b.c > (pv / vol if vol else b.c), "above_ema9": b.c > ema}
    return out


def record(day, pos):
    entry_ts = pos.fills[0][0]
    exit_ts = pos.fills[-1][0]
    return {
        "date": day.isoformat(), "symbol": pos.symbol, "setup": pos.setup, "level": pos.level_name,
        "level_price": round(pos.level, 2), "entry_time": entry_ts.astimezone(ET).strftime("%H:%M"),
        "entry": round(pos.entry, 2), "stop": round(pos.entry - pos.risk, 2), "risk_pct": round(100 * pos.risk / pos.entry, 3),
        "t1": round(pos.t1, 2), "t2": round(pos.t2, 2) if pos.t2 else None, "trimmed": pos.trimmed, "exit_reason": pos.fills[-1][4],
        "minutes": int((exit_ts - entry_ts).total_seconds() // 60), "r": round(pos.realized_r, 3),
        "mfe_r": round((pos.high - pos.entry) / pos.risk, 2) if pos.high else 0.0,
        "mae_r": round((pos.low - pos.entry) / pos.risk, 2) if pos.low != float("inf") else 0.0,
    }


# ---- stats -----------------------------------------------------------------------

def summarize(trades):
    rs = [t["r"] for t in trades]
    if not rs:
        return {"trades": 0}
    wins = [r for r in rs if r > 0.05]
    losses = [r for r in rs if r < -0.05]
    equity = peak = dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    gross_win, gross_loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {
        "trades": len(rs),
        "win_rate": len(wins) / len(rs),
        "scratch_rate": (len(rs) - len(wins) - len(losses)) / len(rs),
        "avg_r": statistics.mean(rs),
        "total_r": sum(rs),
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "max_dd_r": dd,
        "days": len({t["date"] for t in trades}),
    }


def fmt_summary(s):
    if not s.get("trades"):
        return "no trades"
    return (f"{s['trades']} trades, win {s['win_rate']:.0%}, avg {s['avg_r']:+.2f}R, total {s['total_r']:+.1f}R, "
            f"PF {s['profit_factor']:.2f}, maxDD {s['max_dd_r']:.1f}R")


def breakdown(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def write_trades(trades, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if trades:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--symbols", help="comma list; default: every *_5m.csv except SPY/QQQ")
    ap.add_argument("--setups", default="BRH")
    ap.add_argument("--exec", dest="exec_mode", choices=["1m", "5m"], default="1m")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--set", action="append", default=[], metavar="PARAM=VALUE", help="override a Params field")
    ap.add_argument("--trades", help="write trades CSV here")
    args = ap.parse_args(argv)
    symbols = args.symbols.split(",") if args.symbols else default_symbols(args.data)
    params = make_params(args.setups, args.set)
    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None
    trades = run(args.data, symbols, params, args.exec_mode, start, end)
    print(fmt_summary(summarize(trades)))
    for name, key in (("setup", lambda t: t["setup"]), ("symbol", lambda t: t["symbol"]),
                      ("level", lambda t: t["level"]), ("exit", lambda t: t["exit_reason"]),
                      ("month", lambda t: t["date"][:7])):
        print(f"\nby {name}:")
        for k, s in breakdown(trades, key).items():
            print(f"  {k:<24} {fmt_summary(s)}")
    if args.trades:
        write_trades(trades, Path(args.trades))


def default_symbols(data_dir):
    return sorted(p.name.split("_")[0] for p in Path(data_dir).glob("*_5m.csv") if p.name.split("_")[0] not in ("SPY", "QQQ"))


def make_params(setups, overrides):
    params = Params(setups=tuple(setups.split(",")))
    for item in overrides:
        key, value = item.split("=", 1)
        current = getattr(params, key)
        if value.lower() == "none":
            value = None
        elif isinstance(current, bool):
            value = value.lower() in ("1", "true", "yes")
        elif isinstance(current, tuple):
            value = tuple(value.split(","))
        elif isinstance(current, dt.time):
            value = dt.time.fromisoformat(value)
        elif current is None:
            value = None if value.lower() == "none" else float(value)
        else:
            value = type(current)(value)
        params = dataclasses.replace(params, **{key: value})
    return params


if __name__ == "__main__":
    main()
