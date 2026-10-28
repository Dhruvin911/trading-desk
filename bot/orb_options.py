"""The 5-minute ORB (stocks in play) + 10m ribbon signal traded with calls and puts, contracts picked
with the Enrich Trades options pricing checklist, in a $1,000 cash account.

    python3 -m bot.orb_options                    # the checklist defaults, every variant table
    python3 -m bot.orb_options --one --delta 0.4 --dte 7 --universe checklist --trades out.csv

Signals: bot.orb_saty with --or-minutes 5 --relvol --confirm fast --stop atr10 --exit eod
(green 5m range -> call, red -> put; out on the stock stop or at the close).
Contract choice (the guide): delta in 0.30-0.50 (--delta: the strike whose delta is nearest),
enough days that theta can't eat the trade (--dte: first Friday at least that many calendar days
out), spread under 10% of the mid, volume and open interest over 500, IV normal for the stock,
earnings accounted for (--earnings skip: no trade when a report falls before expiry).
--universe all | spread: the tickers whose real spread was under 10% | checklist: spread under 10%
and volume and open interest over 500 (from data/options/chain_snapshot_2026-10-27.json).
Prices: Black-Scholes. IV = 20-day realized vol x 1.2 (x 1.4 for SPY, QQQ), x 1.8 when an
earnings report falls before expiry - what the real chain shows on 2026-10-27. Half the real
spread per fill (spread % of mid from the snapshot, wider by sqrt(0.5 / delta) away from the money,
x --spread-mult). Size: as many contracts as --max-cost buys; no trade if one costs more.
"""
import argparse
import datetime as dt
import json
import math
from argparse import Namespace
from collections import defaultdict

from bot.backtest import ET
from bot.orb_saty import PERIODS, load, simulate
from bot.saty_desk import ROOT
from bot.saty_options import SYMBOLS, bs_call, norm_cdf, realized_vol, run_account, strike_step, years_to_expiry
import bot.orb_saty as orb_saty
from bot.backtest import SymbolData

SNAP = json.loads((ROOT / "data" / "options" / "chain_snapshot_2026-10-27.json").read_text())


def spread_pct(sym):
    c = SNAP["calls"][sym]
    return (c["ask"] - c["bid"]) / ((c["ask"] + c["bid"]) / 2)


UNIVERSE = {
    "all": SYMBOLS.split(","),
    "spread": [s for s in SYMBOLS.split(",") if spread_pct(s) < 0.10],
    "checklist": [s for s in SYMBOLS.split(",") if spread_pct(s) < 0.10 and SNAP["calls"][s]["volume"] >= 500
                  and SNAP["calls"][s]["oi"] >= 500],
}


def delta(kind, s, k, t, vol):
    if t <= 0:
        return 1.0 if (s > k) == (kind == "call") else 0.0
    v = vol * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * v * v) / v
    return norm_cdf(d1) if kind == "call" else norm_cdf(d1) - 1


def price(kind, s, k, t, vol):
    c = bs_call(s, k, t, vol)
    return c if kind == "call" else c - s + k


def expiry_after(day, days):
    d = day + dt.timedelta(days=days)
    while d.weekday() != 4:
        d += dt.timedelta(days=1)
    return d


def earnings_before(sym, day, expiry):
    return any(day <= dt.date.fromisoformat(x) <= expiry for x in SNAP["earnings_pm"].get(sym, []))


def option_trade(t, daily, o):
    sym, day = t["symbol"], t["date"]
    kind = "call" if t["side"] > 0 else "put"
    expiry = expiry_after(day, o.dte)
    earn = earnings_before(sym, day, expiry)
    if earn and o.earnings == "skip":
        return {"skip": "earnings before expiry"}
    vol = max(0.12, realized_vol(daily, day) * (1.4 if sym in ("SPY", "QQQ") else 1.2) * (1.8 if earn else 1.0))
    s0 = t["entry"]
    s1 = s0 + t["side"] * t["r"] * t["risk"]
    t0, t1 = years_to_expiry(t["opened"], expiry), years_to_expiry(t["closed"], expiry)
    step = strike_step(s0)
    ks = [k for k in (math.floor(s0 / step) * step + i * step for i in range(-30, 31)) if k > 0]
    k = min(ks, key=lambda k: abs(abs(delta(kind, s0, k, t0, vol)) - o.delta))
    dl = abs(delta(kind, s0, k, t0, vol))
    p0 = price(kind, s0, k, t0, vol)
    if p0 < 0.05:
        return {"skip": "no premium"}
    half = max(0.005, spread_pct(sym) / 2 * math.sqrt(0.5 / max(dl, 0.1)) * o.spread_mult)
    cost = p0 * (1 + half)
    if cost * 100 > o.max_cost:
        return {"skip": "one contract costs more than the cap"}
    n = int(o.max_cost // (cost * 100))
    p1 = price(kind, s1, k, t1, vol)
    pnl = (p1 * (1 - half) - cost) * 100 * n
    return {"date": day.isoformat(), "symbol": sym, "side": kind, "entry_time": t["opened"].astimezone(ET).strftime("%H:%M"),
            "exit_time": t["closed"].astimezone(ET).strftime("%H:%M"), "stock_r": round(t["r"], 2), "strike": k,
            "expiry": expiry.isoformat(), "delta": round(dl, 2), "iv": round(vol, 2), "premium": round(cost, 2),
            "contracts": n, "cost_usd": round(cost * 100 * n, 2), "pnl_usd": round(pnl, 2),
            "return_pct": round(100 * pnl / (cost * 100 * n), 1), "earnings": earn}


def run(o, signals, daily):
    opts, skips = [], defaultdict(int)
    for t in signals:
        if t["symbol"] not in UNIVERSE[o.universe]:
            continue
        r = option_trade(t, daily[t["symbol"]], o)
        if "skip" in r:
            skips[r["skip"]] += 1
        else:
            opts.append(r)
    opts.sort(key=lambda x: (x["date"], x["entry_time"]))
    acct = Namespace(balance=o.balance, account="cash", max_open=1, max_day_trades=99)
    taken, acct_skips, equity = run_account(opts, acct)
    return opts, taken, equity, skips, acct_skips


def summary(taken, equity, o):
    pnl = sum(t["pnl_usd"] for t in taken)
    peak, dd = o.balance, 0.0
    for _, v in equity:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    per = []
    for k, (a, b) in PERIODS.items():
        ts = [t for t in taken if a.isoformat() <= t["date"] <= b.isoformat()]
        per.append(f"{k} {len(ts)} ${sum(t['pnl_usd'] for t in ts):+,.0f}")
    win = sum(t["pnl_usd"] > 0 for t in taken) / max(1, len(taken))
    avg = sum(t["return_pct"] for t in taken) / max(1, len(taken))
    return (f"{len(taken):3d} trades, win {win:3.0%}, avg {avg:+5.1f}%, ${o.balance:,.0f} -> ${equity[-1][1]:,.0f}, "
            f"max DD ${dd:,.0f} | " + ", ".join(per))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delta", type=float, default=0.40)
    ap.add_argument("--dte", type=int, default=7)
    ap.add_argument("--universe", choices=list(UNIVERSE), default="spread")
    ap.add_argument("--earnings", choices=["skip", "trade"], default="skip")
    ap.add_argument("--max-cost", type=float, default=300.0)
    ap.add_argument("--spread-mult", type=float, default=1.0, help="1.5-2: wider spreads in the first hour")
    ap.add_argument("--confirm", default="fast")
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--one", action="store_true", help="only the settings given")
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    load(SYMBOLS.split(","), 5, True)
    p = {"direction": "candle", "last": "12:00", "entry": "orb", "confirm": o.confirm, "stop": "atr10",
         "exit": "eod", "relvol": True}
    signals = [t for t in (simulate(s, p) for s in orb_saty.SETUPS) if t]
    daily = {s: SymbolData(str(ROOT / "data"), s).daily for s in SYMBOLS.split(",")}
    print(f"{len(signals)} stock signals; universes: " + "; ".join(f"{k}: {','.join(v)}" for k, v in UNIVERSE.items() if k != "all"))
    if o.one:
        opts, taken, equity, skips, acct = run(o, signals, daily)
        print(summary(taken, equity, o))
        print("  skipped:", dict(skips), "| by the account:", dict(acct))
        by = defaultdict(list)
        for t in taken:
            by[t["symbol"]].append(t["pnl_usd"])
        print("  by ticker: " + ", ".join(f"{k} {len(v)} ${sum(v):+,.0f}" for k, v in sorted(by.items(), key=lambda kv: -sum(kv[1]))))
        if o.trades and taken:
            import csv
            with open(o.trades, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(taken[0]))
                w.writeheader()
                w.writerows(taken)
        return
    base = vars(o).copy()
    tables = [("delta", [0.2, 0.3, 0.4, 0.5, 0.7]), ("dte", [2, 7, 14, 28]), ("universe", ["all", "spread", "checklist"]),
              ("earnings", ["skip", "trade"]), ("max_cost", [150.0, 300.0, 500.0]), ("spread_mult", [1.0, 2.0])]
    for name, vals in tables:
        print(f"-- {name} (others at defaults: delta {o.delta}, dte {o.dte}, {o.universe}, earnings {o.earnings}, "
              f"${o.max_cost:.0f}, spread x{o.spread_mult})")
        for v in vals:
            oo = Namespace(**{**base, name: v})
            _, taken, equity, skips, _ = run(oo, signals, daily)
            print(f"  {name}={v!s:9} {summary(taken, equity, oo)}")


if __name__ == "__main__":
    main()
