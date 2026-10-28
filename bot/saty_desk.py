"""Backtest the desk's playbook (strategy.md: Saty ATR Levels + Pivot Ribbon) as the agents trade it.

    python3 -m bot.saty_desk                              # watchlist from the command line, all data
    python3 -m bot.saty_desk --symbols RKLB,TSLA --start 2026-09-14 --trades out.csv
    python3 -m bot.saty_desk --exit13 after_t1 --desk-caps

- Levels: day-mode ATR levels from the previous close and Wilder ATR(14) of daily bars.
- Trend: 10-minute 8/13/21/34/48 EMA ribbon from 5-minute bars of every session (extended hours on).
- Entry candles: 3-minute candles from 1-minute bars on days that have them (Robinhood keeps about six
  weeks), otherwise 5-minute candles. Fills are simulated on the 1-minute bars, or on the 5-minute bars.
- Setups A (golden gate), B (trigger-break checklist) and C (iVomy) as written in strategy.md.
- Exits: a third at each of the first two ATR levels above the entry (stop to breakeven after the
  first), the last third at the full ATR; out on a 10-minute close below the 13 EMA (or only once T1 is
  taken, --exit13 after_t1), everything out on a 10-minute close below the 48; flat at flatten_at.
- --desk-caps applies the desk's portfolio limits afterwards: max entries per day, max open positions,
  and no new entries once the day's realized loss reaches max_daily_loss_usd / risk_per_trade_usd R.
Results are in R. Slippage --slip per fill (default 0.03%).
"""
import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from bot.backtest import ET, RTH_CLOSE, RTH_OPEN, SymbolData, breakdown, fmt_summary, summarize, wilder_atr, write_trades
from bot.strategy import ATR, EMA, Bar, Params, find_flag, saty_levels
from bot.timewarp import bars_by_period

ROOT = Path(__file__).resolve().parent.parent
FIBS_UP = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.236)


def ttm_squeeze(closes, highs, lows, trs, n=20, bb_mult=2.0, kc_mult=1.5):
    """TTM squeeze on the last `n` bars: (squeeze_on, momentum). Squeeze on = Bollinger Bands
    (n, bb_mult) inside Keltner Channels (n, kc_mult x ATR); momentum = linear regression value, at
    the last bar, of close - mean(midpoint of the n-bar range, n-bar SMA) - as in John Carter's TTM
    Squeeze and Saty's Squeeze Pro (mid compression)."""
    if len(closes) < 2 * n:
        return None, None
    c = closes[-n:]
    sma = sum(c) / n
    std = (sum((x - sma) ** 2 for x in c) / n) ** 0.5
    atr = sum(trs[-n:]) / n
    on = sma + bb_mult * std < sma + kc_mult * atr and sma - bb_mult * std > sma - kc_mult * atr
    vals = []
    for i in range(n):
        j = len(closes) - n + i
        hh, ll = max(highs[j - n + 1:j + 1]), min(lows[j - n + 1:j + 1])
        sm = sum(closes[j - n + 1:j + 1]) / n
        vals.append(closes[j] - ((hh + ll) / 2 + sm) / 2)
    xm, ym = (n - 1) / 2, sum(vals) / n
    slope = sum((i - xm) * (v - ym) for i, v in enumerate(vals)) / sum((i - xm) ** 2 for i in range(n))
    return on, ym + slope * (n - 1 - xm)


def ribbon_series(d):
    """[(close_time_utc, {ema: value, "sq_on": bool, "mom": float}, 10m bar)] over every 5m bar,
    all sessions, carried across days."""
    e = {n: EMA(n) for n in (8, 13, 21, 34, 48)}
    out = []
    closes, highs, lows, trs = [], [], [], []
    rows = [(t, b) for day in sorted(d.m5) for t, b, _ in d.m5[day]]
    for _, close_t, b10 in bars_by_period(rows, 10):
        trs.append(b10.h - b10.l if not closes else max(b10.h - b10.l, abs(b10.h - closes[-1]), abs(b10.l - closes[-1])))
        closes.append(b10.c)
        highs.append(b10.h)
        lows.append(b10.l)
        v = {n: x.update(b10.c) for n, x in e.items()}
        v["sq_on"], v["mom"] = ttm_squeeze(closes[-60:], highs[-60:], lows[-60:], trs[-60:])
        out.append((close_t, v, b10))
    return out


def squeeze_ok(mode, hist10):
    """Squeeze filter on the 10m bars so far (hist10: [(ribbon values, close)], newest last)."""
    if mode == "none":
        return True
    if len(hist10) < 7 or hist10[-1][0]["mom"] is None or hist10[-2][0]["mom"] is None:
        return False
    now, prev = hist10[-1][0], hist10[-2][0]
    mom_up = now["mom"] > 0 and now["mom"] >= prev["mom"]
    recent_on = any(h[0]["sq_on"] for h in hist10[-7:-1])
    if mode == "momentum":
        return mom_up
    if mode == "fired":            # squeeze released within the last hour with momentum up
        return mom_up and recent_on and not now["sq_on"]
    if mode == "on_or_fired":      # in a squeeze, or just fired, with momentum up
        return mom_up and (now["sq_on"] or recent_on)
    raise ValueError(mode)


class Trade:
    def __init__(self, sym, day, setup, entry, stop, targets, ts):
        self.sym, self.day, self.setup = sym, day, setup
        self.entry, self.stop, self.risk = entry, stop, entry - stop
        self.targets = targets              # [(name, price, fraction)]
        self.left, self.r, self.done = 1.0, 0.0, 0
        self.opened, self.closed, self.reason = ts, None, None
        self.high = entry
        self.fills = []                     # (time, price, fraction of the position, reason)

    def sell(self, price, frac, ts, reason):
        self.fills.append((ts.isoformat(), round(price, 4), round(frac, 4), reason))
        self.r += frac * (price - self.entry) / self.risk
        self.left -= frac
        if self.left <= 1e-9:
            self.closed, self.reason = ts, reason

    def record(self):
        return {"date": self.day.isoformat(), "symbol": self.sym, "setup": self.setup,
                "entry_time": self.opened.astimezone(ET).strftime("%H:%M"),
                "exit_time": self.closed.astimezone(ET).strftime("%H:%M") if self.closed else "",
                "entry": round(self.entry, 2), "stop": round(self.entry - self.risk, 2),
                "targets": " ".join(f"{n}@{p:.2f}" for n, p, _ in self.targets), "levels_hit": self.done,
                "exit_reason": self.reason, "r": round(self.r, 3),
                "mfe_r": round((self.high - self.entry) / self.risk, 2),
                "opened_at": self.opened.isoformat(), "fills": json.dumps(self.fills)}


def in_windows(t, windows):
    return any(a <= t < b for a, b in windows)


def mirrored(d, center=None):
    """The same symbol reflected around `center` (price -> 2*center - price, highs and lows swapped).
    Running the long-only rules on it gives the bearish mirror of every setup (put trigger, short
    golden gate, bearish ribbon, Vomy, bear flags ...); map prices back with the same reflection."""
    import copy
    c = center if center is not None else d.daily[-1][4]
    m = copy.copy(d)
    flip = lambda b: Bar(b.ts, 2 * c - b.o, 2 * c - b.l, 2 * c - b.h, 2 * c - b.c, b.v)
    m.m5 = {day: [(t, flip(b), s) for t, b, s in rows] for day, rows in d.m5.items()}
    m.m1 = {day: [(t, flip(b), s) for t, b, s in rows] for day, rows in d.m1.items()}
    m.daily = [(day, 2 * c - o, 2 * c - l, 2 * c - h, 2 * c - cl) for day, o, h, l, cl in d.daily]
    m.center = c
    return m


def run_symbol(data_dir, sym, cfg, opts, data=None):
    d = data or SymbolData(data_dir, sym)
    rib = ribbon_series(d)
    windows = [(dt.time.fromisoformat(w["start"]), dt.time.fromisoformat(w["end"])) for w in cfg["entry_windows"]]
    flatten = dt.time.fromisoformat(cfg["flatten_at"])
    min_rr = cfg["risk"]["min_reward_risk"]
    max_range_a, max_range_b = 0.9, cfg["strategy"].get("max_range_used", 0.7)
    slip = opts.slip
    trades = []
    ri = 0
    e = last10 = None
    hist10 = []                             # recent 10m (ribbon, close) for iVomy
    candle_atr = ATR(14)
    days = sorted(d.m5)
    for day in days:
        if (opts.start and day < opts.start) or (opts.end and day > opts.end):
            continue
        prior = [r for r in d.daily if r[0] < day]
        if len(prior) < 20:
            continue
        pdc, atr = prior[-1][4], wilder_atr(prior)
        if opts.daily_squeeze != "none":
            dc, dh, dl = [r[4] for r in prior[-60:]], [r[2] for r in prior[-60:]], [r[3] for r in prior[-60:]]
            dtr = [dh[0] - dl[0]] + [max(h - l, abs(h - pc0), abs(l - pc0)) for h, l, pc0 in zip(dh[1:], dl[1:], dc)]
            d_on, d_mom = ttm_squeeze(dc, dh, dl, dtr)
            p_on, p_mom = ttm_squeeze(dc[:-1], dh[:-1], dl[:-1], dtr[:-1])
            hist_on = [ttm_squeeze(dc[:k], dh[:k], dl[:k], dtr[:k])[0] for k in range(len(dc) - 6, len(dc))]
            mom_up = d_mom is not None and p_mom is not None and d_mom > 0 and d_mom >= p_mom
            ok = {"momentum": mom_up, "fired": mom_up and any(hist_on[:-1]) and not d_on,
                  "on_or_fired": mom_up and (d_on or any(hist_on[:-1]))}[opts.daily_squeeze]
            if not ok:
                continue
        lv = saty_levels(pdc, atr)
        trigger, gate, full = lv[0.236], lv[0.382], lv[1.0]
        m1 = [(t, b) for t, b, _ in d.m1.get(day, []) if RTH_OPEN <= t.time() < RTH_CLOSE]
        use_1m = sum(b.v for _, b in m1) > 0 and len(m1) >= 300
        if use_1m:
            fine = m1
            candles = bars_by_period(m1, 3)
            step = dt.timedelta(minutes=1)
        else:
            fine = d.rth(day)
            candles = [(t, t + dt.timedelta(minutes=5), b) for t, b in fine]
            step = dt.timedelta(minutes=5)
        if not fine:
            continue
        candle_close = {c[1]: c for c in candles}
        hod = lod = None
        triggered_10m = False               # a 10m close above the call trigger today
        prev_close = None
        broken = {}                         # level price -> True once a candle closed through it today
        or_high = None                      # opening range: the first 10 minutes
        day_candles = []
        key_levels = [lv[f] for f in (0.0, 0.236, 0.382, 0.5, 0.618, 0.786)]
        if opts.sr_levels:                  # classic support / resistance: previous day high, premarket high
            pre = [b for _, b, sess in d.m5.get(day, []) if sess == "pre"]
            key_levels += [prior[-1][2]] + ([max(b.h for b in pre)] if pre else [])
            key_levels = sorted(set(round(x, 2) for x in key_levels))
        piv_hi, piv_lo = [], []             # confirmed swing highs / lows on the entry candles: (index, price)
        pos_hh = False                      # a higher swing high was made while the position is open
        pos, armed, n_today = None, None, 0
        for t, b in fine:
            end = t + step
            lt = end.astimezone(ET).time()
            # fills / stops / targets on this bar
            if armed and pos is None and b.h >= armed["entry"]:
                fill = max(armed["entry"], b.o) * (1 + slip)
                if fill - armed["entry"] <= 0.5 * (armed["entry"] - armed["stop"]):
                    pos = Trade(sym, day, armed["setup"], fill, armed["stop"], armed["targets"], t)
                    n_today += 1
                armed = None
                same_bar_green = b.c >= b.o
            else:
                same_bar_green = False
            if pos and pos.closed is None:
                pos.high = max(pos.high, b.h)
                if not same_bar_green and b.l <= pos.stop:
                    pos.sell(min(pos.stop, b.o) * (1 - slip), pos.left, t, "stop" if pos.done == 0 else "break-even stop")
                else:
                    while pos.done < len(pos.targets) and b.h >= pos.targets[pos.done][1]:
                        name, price, frac = pos.targets[pos.done]
                        pos.sell(max(price, b.o) * (1 - slip), min(frac, pos.left), t, f"target {name}")
                        pos.done += 1
                        if pos.done == 1:
                            pos.stop = max(pos.stop, pos.entry)
                        if pos.closed:
                            break
                if pos.closed:
                    trades.append(pos.record())
                    pos = None
            hod = b.h if hod is None else max(hod, b.h)
            lod = b.l if lod is None else min(lod, b.l)
            # 10m ribbon closes up to now (premarket included)
            new10 = None
            while ri < len(rib) and rib[ri][0] <= end:
                e, last10 = rib[ri][1], rib[ri][2]
                new10 = last10
                hist10.append((e, last10.c))
                hist10 = hist10[-24:]
                ri += 1
            if e is None:
                continue
            if new10 is not None and new10.ts.astimezone(ET).date() == day and new10.ts.astimezone(ET).time() >= RTH_OPEN:
                if new10.c > trigger:
                    triggered_10m = True
                if pos:
                    if new10.c < e[48]:
                        pos.sell(b.c * (1 - slip), pos.left, t, "10m close below the 48")
                    elif new10.c < e[13] and (opts.exit13 == "always" or pos.done > 0):
                        pos.sell(b.c * (1 - slip), pos.left, t, "10m close below the 13")
                    if pos.closed:
                        trades.append(pos.record())
                        pos = None
            if lt >= flatten:
                armed = None
                if pos:
                    pos.sell(b.c * (1 - slip), pos.left, t, "flatten")
                    trades.append(pos.record())
                    pos = None
                break
            # entry decisions on completed entry candles
            c = candle_close.get(end)
            if c is None:
                continue
            ct, _, cb = c
            a_c = candle_atr.update(cb)
            if armed and armed["expires"] <= end:
                armed = None
            pc, prev_close = prev_close, cb.c
            day_candles.append(cb)
            i = len(day_candles) - 1
            new_low = False
            if i >= 4:                      # 2-candle fractal, confirmed two candles later
                mid = day_candles[i - 2]
                around = day_candles[i - 4:i - 2] + day_candles[i - 1:i + 1]
                if all(mid.h > x.h for x in around):
                    piv_hi.append((i - 2, mid.h))
                    if pos and len(piv_hi) >= 2 and piv_hi[-1][1] > piv_hi[-2][1]:
                        pos_hh = True
                if all(mid.l < x.l for x in around):
                    piv_lo.append((i - 2, mid.l))
                    new_low = True
            if (opts.structure_exit and pos and new_low and pos_hh and len(piv_lo) >= 2
                    and piv_lo[-1][1] < piv_lo[-2][1]):
                pos.sell(cb.c * (1 - slip), pos.left, ct, "structure: lower low after a higher high")
                trades.append(pos.record())
                pos, pos_hh = None, False
            if pos is None:
                pos_hh = False
            if lt <= dt.time(9, 40):
                or_high = cb.h if or_high is None else max(or_high, cb.h)
            new_breaks = [L for L in key_levels if pc is not None and pc <= L < cb.c]
            for L in new_breaks:
                broken[L] = True
            if pos or armed or n_today >= opts.max_per_symbol or not in_windows(lt, windows):
                continue
            if cb.c <= trigger or cb.c <= pdc or cb.c >= full:          # trigger box / below PDC / spent
                continue
            used = (hod - lod) / atr
            stacked = e[8] > e[13] > e[21] > e[34] > e[48] and last10.c > e[48]
            tol = 0.1 * a_c
            pullback = cb.l <= e[8] + tol and cb.l > e[48] - tol and cb.c > e[13]
            reclaim_gate = pc is not None and pc < gate < cb.c and cb.l > e[48]
            ctx_a = stacked and hod >= gate and used < max_range_a
            ctx_b = triggered_10m and cb.c > max(e[34], e[48]) and used < max_range_b
            bull = (stacked or ctx_b) and used < max_range_a
            decisive = cb.h > cb.l and (cb.c - cb.l) / (cb.h - cb.l) >= 0.5
            ctx = "A" if ctx_a else ("B" if ctx_b else "trend")
            setup = kind = None
            entry, base = round(cb.h + 0.01, 2), min(cb.l, e[21])
            for k in opts.entries:
                if k == "pullback" and (ctx_a or ctx_b) and (pullback or (ctx_a and reclaim_gate)):
                    kind = k
                elif k == "retest" and bull:
                    L = next((L for L in key_levels if broken.get(L) and L not in new_breaks
                              and cb.l <= L + tol and cb.c > L), None)
                    if L is not None:
                        kind, base = k, min(cb.l, L)
                elif k == "break" and bull and new_breaks and decisive:
                    L = max(new_breaks)
                    nxt = next((x for x in sorted(lv.values()) if x > L), None)
                    if nxt and cb.h - L <= 0.5 * (nxt - L):          # broke it, not already running away
                        kind, base = k, min(cb.l, L)
                elif k == "orb" and bull and or_high and lt > dt.time(9, 40) and pc is not None and pc <= or_high < cb.c and decisive:
                    kind = k
                elif k == "trendline" and bull and len(piv_hi) >= 2 and pc is not None and decisive:
                    (i1, h1), (i2, h2) = piv_hi[-2], piv_hi[-1]
                    if h2 < h1 and i2 - i1 >= 3:                  # falling line through two lower highs
                        slope = (h2 - h1) / (i2 - i1)
                        now, before = h2 + slope * (i - i2), h2 + slope * (i - 1 - i2)
                        if pc <= before and cb.c > now and cb.h - now <= 2 * a_c:
                            kind = k
                elif k == "flag" and bull:
                    f = find_flag(day_candles, a_c, Params(), bull=True)
                    if f and f[1] > e[21] - tol:
                        kind, entry, base = k, round(f[0] + 0.01, 2), f[1]
                if kind:
                    setup = f"{ctx}-{kind}"
                    break
            if setup is None and opts.ivomy and len(hist10) >= 12:
                was_bear = any(r[13] < r[48] and c10 < r[48] for r, c10 in hist10[:-3])
                tested48 = last10.h >= e[48]
                if was_bear and tested48 and cb.l <= e[13] + tol and cb.c > e[13] and cb.c > e[8]:
                    setup, base = "C-ivomy", cb.l
            if setup is None or not squeeze_ok(opts.squeeze, hist10):
                continue
            stop = round(base - max(0.01, 0.05 * a_c), 2)
            risk = entry - stop
            if risk <= 0 or risk < 0.001 * entry:
                continue
            ups = [(f"+{f:.1%}", lv[f]) for f in FIBS_UP if lv[f] >= entry + opts.min_target_r * risk]
            if not ups or max(p for _, p in ups[:6]) < entry + min_rr * risk:
                continue
            first, second = ups[0], ups[1] if len(ups) > 1 else None
            last = ("+100.0%", full) if full > (second[1] if second else first[1]) else None
            tg = [(first[0], first[1], 1 / 3)]
            if second:
                tg.append((second[0], second[1], 1 / 3))
            if last:
                tg.append((last[0], last[1], 1 / 3))
            tg[-1] = (tg[-1][0], tg[-1][1], 1.0)                      # whatever is left
            armed = {"setup": setup, "entry": entry, "stop": stop, "targets": tg,
                     "expires": end + 2 * (dt.timedelta(minutes=3) if use_1m else dt.timedelta(minutes=5))}
        if pos:
            pos.sell(fine[-1][1].c * (1 - slip), pos.left, fine[-1][0], "end of data")
            trades.append(pos.record())
    return trades


def desk_caps(trades, cfg):
    """Keep trades the desk's portfolio limits would have allowed, in time order."""
    r = cfg["risk"]
    loss_cap = r["max_daily_loss_usd"] / r["risk_per_trade_usd"]
    kept = []
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    for day in sorted(by_day):
        open_, realized, n = [], 0.0, 0
        for t in sorted(by_day[day], key=lambda x: x["entry_time"]):
            closed_now = [o for o in open_ if o["exit_time"] <= t["entry_time"]]
            for o in closed_now:
                realized += o["r"]
                open_.remove(o)
            if n >= r["max_entries_per_day"] or len(open_) >= r["max_open_positions"] or realized <= -loss_cap:
                continue
            kept.append(t)
            open_.append(t)
            n += 1
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--symbols", default="RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR")
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 3, 9))
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--slip", type=float, default=0.0003)
    ap.add_argument("--exit13", choices=["always", "after_t1"], default="always")
    ap.add_argument("--no-ivomy", dest="ivomy", action="store_false")
    ap.add_argument("--entries", type=lambda v: v.split(","),
                    default=["pullback", "retest", "break", "trendline", "orb", "flag"],
                    help="entry types, in priority order: pullback,retest,break,trendline,orb,flag")
    ap.add_argument("--no-sr", dest="sr_levels", action="store_false",
                    help="don't add the previous day high and premarket high to the levels for breaks/retests")
    ap.add_argument("--no-structure-exit", dest="structure_exit", action="store_false",
                    help="don't exit on a lower swing low after a higher swing high")
    ap.add_argument("--max-per-symbol", type=int, default=2)
    ap.add_argument("--min-target-r", type=float, default=0.0,
                    help="scale-out levels must be at least this many R above the entry")
    ap.add_argument("--desk-caps", action="store_true")
    ap.add_argument("--daily-squeeze", choices=["none", "momentum", "fired", "on_or_fired"], default="none",
                    help="TTM squeeze filter on the daily bars up to the previous close")
    ap.add_argument("--squeeze", choices=["none", "momentum", "fired", "on_or_fired"], default="none",
                    help="TTM squeeze filter on the 10m bars (momentum up; fired recently; in or just out)")
    ap.add_argument("--trades")
    opts = ap.parse_args(argv)
    cfg = json.loads((ROOT / "desk" / "config.json").read_text())
    trades = []
    for sym in opts.symbols.split(","):
        trades += run_symbol(opts.data, sym, cfg, opts)
    trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    if opts.desk_caps:
        trades = desk_caps(trades, cfg)
    print(fmt_summary(summarize(trades)))
    for name, key in (("setup", lambda t: t["setup"]), ("entry type", lambda t: t["setup"].split("-")[1]),
                      ("symbol", lambda t: t["symbol"]),
                      ("exit", lambda t: t["exit_reason"]), ("month", lambda t: t["date"][:7])):
        print(f"\nby {name}:")
        for k, s in breakdown(trades, key).items():
            print(f"  {k:<26} {fmt_summary(s)}")
    if opts.trades:
        write_trades(trades, Path(opts.trades))


if __name__ == "__main__":
    main()
