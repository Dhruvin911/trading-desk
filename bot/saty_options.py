"""Replay the desk playbook's trades (bot.saty_desk) with call options, in a small account.

    python3 -m bot.saty_options                         # $1,000 margin account, PDT limit
    python3 -m bot.saty_options --account cash          # cash account: no PDT, proceeds settle next day
    python3 -m bot.saty_options --max-cost 100 --risk 20 --trades out.csv

Option prices come from Black-Scholes, because Robinhood keeps no intraday history for expired
contracts. Volatility = 20-day realized volatility of daily closes x --iv-mult (implied vol usually
runs above realized; RKLB's real Oct 30 calls on 2026-10-27 fit about 1.4x), floored at 20%. Each fill pays half the bid/ask spread: --spread x premium,
at least $0.02. No interest, no dividends (calls are bought and sold the same day).

Contract choice, as Saty describes it for directional day trades: the first strike above the price
(slightly out of the money), expiring on the first Friday at least --min-dte calendar days out ("get
more time than you think you need"). If one contract costs more than --max-cost, the strike moves up,
but never past the --max-strike-level-th target level above the entry (Saty: "no more than one or two
levels away"); otherwise the trade is skipped as too expensive.

Sizing: contracts = how many keep the loss at the technical stop within --risk dollars (at least one
if a single contract's stop loss is within 1.5x --risk), and no more than --max-cost in premium.
Scale-outs at the ATR levels sell whole contracts in proportion (one contract can't be split).
"""
import argparse
import csv
import datetime as dt
import json
import math
from argparse import Namespace
from collections import defaultdict
from pathlib import Path

from bot.backtest import ET, SymbolData
from bot.saty_desk import ROOT, desk_caps, run_symbol

SYMBOLS = "RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR"


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(s, k, t_years, vol):
    if t_years <= 0:
        return max(0.0, s - k)
    v = vol * math.sqrt(t_years)
    d1 = (math.log(s / k) + 0.5 * v * v) / v
    return s * norm_cdf(d1) - k * norm_cdf(d1 - v)


def strike_step(price):
    return 0.5 if price < 25 else 1.0 if price < 200 else 2.5 if price < 500 else 5.0


def expiry_for(day, min_dte):
    d = day + dt.timedelta(days=min_dte)
    while d.weekday() != 4:
        d += dt.timedelta(days=1)
    return d


def years_to_expiry(ts, expiry):
    close = dt.datetime.combine(expiry, dt.time(16, 0), tzinfo=ET)
    return max(0.0, (close - ts.astimezone(ET)).total_seconds()) / (365 * 86400)


def realized_vol(daily, day, n=20):
    closes = [r[4] for r in daily if r[0] < day][-(n + 1):]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    m = sum(rets) / len(rets)
    return math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1) * 252)


def option_trade(t, daily, o):
    """Price one stock trade as a call trade. Returns a dict or None (skipped, with why)."""
    day = dt.date.fromisoformat(t["date"])
    opened = dt.datetime.fromisoformat(t["opened_at"])
    s0, stop = t["entry"], t["stop"]
    vol = max(0.2, realized_vol(daily, day) * o.iv_mult)
    expiry = expiry_for(day, o.min_dte)
    levels = [float(x.split("@")[1]) for x in t["targets"].split()]
    strike_cap = levels[min(o.max_strike_level, len(levels)) - 1]     # Saty: one or two levels away
    step = strike_step(s0)
    k = math.floor(s0 / step) * step + step                     # first strike above the price
    half = lambda p: max(0.02, o.spread * p)
    while True:
        prem = bs_call(s0, k, years_to_expiry(opened, expiry), vol)
        cost = prem + half(prem)
        if cost * 100 <= o.max_cost:
            break
        k += step
        if k > strike_cap:
            return {"skip": "too expensive"}
    if prem < 0.05:
        return {"skip": "no premium"}
    later = opened + dt.timedelta(minutes=30)
    at_stop = bs_call(stop, k, years_to_expiry(later, expiry), vol)
    loss1 = (cost - (at_stop - half(at_stop))) * 100
    n = min(int(o.max_cost // (cost * 100)), int(o.risk // loss1) if loss1 > 0 else 99)
    if n < 1:
        if loss1 <= 1.5 * o.risk and cost * 100 <= o.max_cost:
            n = 1
        else:
            return {"skip": "stop loss too big for the risk budget"}
    pnl, sold, cum = -cost * 100 * n, 0, 0.0
    fills = json.loads(t["fills"])
    for i, (ts, price, frac, reason) in enumerate(fills):
        cum += frac
        target_sold = n if i == len(fills) - 1 else min(n, round(cum * n))
        q = target_sold - sold
        if q <= 0:
            continue
        v = bs_call(price, k, years_to_expiry(dt.datetime.fromisoformat(ts), expiry), vol)
        pnl += max(0.0, v - half(v)) * 100 * q
        sold += q
    return {"date": t["date"], "symbol": t["symbol"], "setup": t["setup"], "entry_time": t["entry_time"],
            "exit_time": t["exit_time"], "stock_r": t["r"], "strike": k, "expiry": expiry.isoformat(),
            "iv": round(vol, 3), "premium": round(cost, 2), "contracts": n, "cost_usd": round(cost * 100 * n, 2),
            "pnl_usd": round(pnl, 2), "return_pct": round(100 * pnl / (cost * 100 * n), 1),
            "exit_reason": t["exit_reason"]}


def business_days_back(day, n):
    out, d = [], day
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return set(out)


def run_account(trades, o):
    """Walk the option trades in time order through a small account."""
    cash, settled_next = o.balance, 0.0
    equity = [(None, cash)]
    taken, skipped = [], defaultdict(int)
    day_trades = []                                             # dates of day trades
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    for day in sorted(by_day):
        d = dt.date.fromisoformat(day)
        cash += settled_next                                    # yesterday's proceeds settle (cash account)
        settled_next = 0.0
        open_ = []
        for t in sorted(by_day[day], key=lambda x: x["entry_time"]):
            for x in [x for x in open_ if x["exit_time"] <= t["entry_time"]]:
                open_.remove(x)
            if len(open_) >= o.max_open:
                skipped["max open positions"] += 1
                continue
            if o.account == "margin" and cash < 25000:
                recent = [x for x in day_trades if x in business_days_back(d, 5)]
                if len(recent) >= o.max_day_trades:
                    skipped["PDT: 3 day trades in 5 days"] += 1
                    continue
            if t["cost_usd"] > cash:
                skipped["not enough cash"] += 1
                continue
            cash -= t["cost_usd"]
            proceeds = t["cost_usd"] + t["pnl_usd"]
            if o.account == "cash":
                settled_next += proceeds
            else:
                cash += proceeds
            day_trades.append(d)
            open_.append(t)
            taken.append(t)
        equity.append((day, cash + settled_next))
    return taken, skipped, equity


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 3, 9))
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--account", choices=["margin", "cash"], default="margin")
    ap.add_argument("--max-day-trades", type=int, default=3)
    ap.add_argument("--max-open", type=int, default=2)
    ap.add_argument("--max-cost", type=float, default=150.0, help="most premium per trade, dollars")
    ap.add_argument("--risk", type=float, default=30.0, help="loss at the technical stop per trade, dollars")
    ap.add_argument("--min-dte", type=int, default=4)
    ap.add_argument("--max-strike-level", type=int, default=2, help="strike at most this many target levels up")
    ap.add_argument("--iv-mult", type=float, default=1.3, help="implied vol = realized vol x this (RKLB 2026-10-27 calls fit ~1.4)")
    ap.add_argument("--spread", type=float, default=0.025, help="half the bid/ask spread, share of premium")
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    cfg = json.loads((ROOT / "desk" / "config.json").read_text())
    so = Namespace(start=o.start, end=o.end, slip=0.0, exit13="always", ivomy=True, max_per_symbol=2, min_target_r=0.0,
                   entries=["pullback", "retest", "break", "trendline", "orb", "flag"], squeeze="none",
                   daily_squeeze="none", sr_levels=True, structure_exit=True)
    opt_trades, skips = [], defaultdict(int)
    for sym in o.symbols.split(","):
        daily = SymbolData(str(ROOT / "data"), sym).daily
        for t in run_symbol(str(ROOT / "data"), sym, cfg, so):
            r = option_trade(t, daily, o)
            if "skip" in r:
                skips[r["skip"]] += 1
            else:
                opt_trades.append(r)
    opt_trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    taken, acct_skips, equity = run_account(opt_trades, o)
    wins = [t for t in taken if t["pnl_usd"] > 0]
    pnl = sum(t["pnl_usd"] for t in taken)
    peak, dd = o.balance, 0.0
    for _, v in equity:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    print(f"{o.account} account, start ${o.balance:,.0f}: {len(taken)} option trades, win {len(wins) / max(1, len(taken)):.0%}, "
          f"P&L ${pnl:+,.0f}, end ${equity[-1][1]:,.0f}, max drawdown ${dd:,.0f}")
    if taken:
        print(f"  avg cost ${sum(t['cost_usd'] for t in taken) / len(taken):.0f}, avg contracts "
              f"{sum(t['contracts'] for t in taken) / len(taken):.1f}, avg return per trade "
              f"{sum(t['return_pct'] for t in taken) / len(taken):+.1f}%, best ${max(t['pnl_usd'] for t in taken):+.0f}, "
              f"worst ${min(t['pnl_usd'] for t in taken):+.0f}")
    print(f"  all signals priced: {len(opt_trades)}, win {sum(t['pnl_usd'] > 0 for t in opt_trades) / max(1, len(opt_trades)):.0%}, "
          f"avg ${sum(t['pnl_usd'] for t in opt_trades) / max(1, len(opt_trades)):+.2f} per trade")
    print("  skipped before the account:", dict(skips))
    print("  skipped by the account:", dict(acct_skips))
    months = defaultdict(lambda: [0, 0.0])
    for t in taken:
        months[t["date"][:7]][0] += 1
        months[t["date"][:7]][1] += t["pnl_usd"]
    for m in sorted(months):
        print(f"  {m}: {months[m][0]} trades, ${months[m][1]:+,.0f}")
    if o.trades:
        with open(o.trades, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(opt_trades[0]))
            w.writeheader()
            w.writerows(taken)


if __name__ == "__main__":
    main()
