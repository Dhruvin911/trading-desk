"""15-minute ORB with Saty ATR levels on Micro Nasdaq futures (MNQ), in a $1,000 account.

    python3 -m bot.orb_mnq                       # every combination, build Mar-Jul, check Aug, Sep-Oct
    python3 -m bot.orb_mnq --one --entry trigger --stop level --exit tp1 --trades out.csv

No futures history is available here, so QQQ stands in: MNQ points = QQQ price x 41.1 (NDX / QQQ
at the month ends Apr-Sep 2026 was 41.08-41.11), $2 a point. ATR levels, the opening range and the
ribbon are QQQ's (regular-hours close as the previous close); relative volume is QQQ's.
Rules come from bot.orb_saty: 15m range (9:30-9:45) sets the side (--direction candle) or either
break counts (both); entry orb / trigger / level; 10m ribbon confirm none / fast; stop atr10 /
or_mid / level; exit tp1 / tp2 (everything at the next / second ATR level), eod, r13 (10m close
under the 13 EMA); entries until 12:00.
Account: contracts = how many keep the stop loss within --risk of equity, no more than equity /
--margin; no trade if one contract risks more than that. Costs per contract per round trip:
--fees ($0.50 commission + exchange fees a side) and one tick ($0.50) of slippage each side.
"""
import argparse
import itertools
from collections import defaultdict
from multiprocessing import Pool

import bot.orb_saty as orb_saty
from bot.orb_saty import PERIODS, load, simulate

RATIO, POINT = 41.1, 2.0
GRID = {"direction": ["candle", "both"], "entry": ["orb", "trigger", "level"], "confirm": ["none", "fast"],
        "stop": ["atr10", "or_mid", "level"], "exit": ["tp1", "tp2", "eod", "r13"], "relvol": [False, True]}


def account(trades, o):
    eq, peak, dd, taken, skipped = o.balance, o.balance, 0.0, [], 0
    for t in trades:
        risk_usd = t["risk"] * RATIO * POINT + o.cost             # one contract, stop hit
        n = min(int(o.risk * eq // risk_usd), int(eq // o.margin))
        if n < 1:
            skipped += 1
            continue
        pnl = n * (t["r"] * t["risk"] * RATIO * POINT - o.cost)
        eq += pnl
        peak, dd = max(peak, eq), min(dd, eq - peak)
        taken.append({**t, "contracts": n, "pnl_usd": round(pnl, 2), "equity": round(eq, 2)})
        if eq < o.margin:
            break                                                 # can't hold one contract any more
    return taken, eq, dd, skipped


def evaluate(args):
    p, o = args
    trades = sorted((t for t in (simulate(s, p) for s in orb_saty.SETUPS) if t), key=lambda t: t["opened"])
    out = {}
    for name, (a, b) in PERIODS.items():
        ts = [t for t in trades if a <= t["date"] <= b]
        taken, eq, dd, skipped = account(ts, o)                   # each period from a fresh $1,000
        pts = [t["r"] * t["risk"] * RATIO - o.cost / POINT for t in ts]         # net of fees and slippage
        out[name] = {"n": len(ts), "pts": sum(pts) / max(1, len(pts)), "end": eq, "dd": dd,
                     "win": sum(x > 0 for x in pts) / max(1, len(pts)), "taken": len(taken)}
    taken, eq, dd, _ = account(trades, o)
    out["all"] = {"end": eq, "dd": dd, "n": len(trades), "taken": len(taken)}
    return p, out, taken


def fmt(p, r):
    cells = " | ".join(f"{k} {r[k]['n']:3d} {r[k]['pts']:+6.1f}pt win {r[k]['win']:3.0%} ${r[k]['end']:,.0f}" for k in PERIODS)
    return (f"{cells} | all ${r['all']['end']:,.0f} DD ${r['all']['dd']:,.0f}  "
            + " ".join(f"{k}={p[k]}" for k in GRID))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=0.10, help="most of equity to lose at the stop")
    ap.add_argument("--margin", type=float, default=1000.0, help="equity needed per contract (check Robinhood)")
    ap.add_argument("--fees", type=float, default=1.70, help="commission + exchange fees per contract round trip")
    ap.add_argument("--one", action="store_true")
    for k, v in GRID.items():
        if k == "relvol":
            ap.add_argument("--relvol", action="store_true")
        else:
            ap.add_argument("--" + k, choices=v, default=v[0])
    ap.add_argument("--last", default="12:00")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--trades")
    o = ap.parse_args(argv)
    o.cost = o.fees + 2 * 0.25 * POINT                              # fees + one tick each side
    load(["QQQ"], 15, True)
    print(f"QQQ as MNQ (x{RATIO}, ${POINT:.0f}/pt), ${o.balance:,.0f}, risk {o.risk:.0%}, ${o.margin:,.0f} per contract; "
          "columns per period (fresh $1,000 each): trades, avg MNQ points/trade, win, end")
    if o.one:
        p = {"last": o.last, **{k: getattr(o, k) for k in GRID}}
        p, r, taken = evaluate((p, o))
        print(fmt(p, r))
        by = defaultdict(lambda: [0, 0.0])
        for t in taken:
            by[t["reason"]][0] += 1
            by[t["reason"]][1] += t["pnl_usd"]
        print("  exits: " + ", ".join(f"{k} {n} ${v:+,.0f}" for k, (n, v) in by.items()))
        if o.trades and taken:
            import csv
            with open(o.trades, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "side", "entry", "r", "reason", "contracts", "pnl_usd", "equity"],
                                   extrasaction="ignore")
                w.writeheader()
                w.writerows(taken)
        return
    combos = [dict({"last": o.last}, **dict(zip(GRID, v))) for v in itertools.product(*GRID.values())]
    with Pool() as pool:
        results = [(p, r) for p, r, _ in pool.map(evaluate, [(c, o) for c in combos])]
    ok = [(p, r) for p, r in results if r["build"]["n"] >= 30]
    ok.sort(key=lambda pr: -pr[1]["build"]["pts"])
    print(f"{len(combos)} combinations, {len(ok)} with 30+ build trades; best by build points/trade:")
    for p, r in ok[:o.top]:
        print(" ", fmt(p, r))
    pos = lambda k: sum(r[k]["pts"] > 0 for _, r in ok)
    print(f"positive points/trade: build {pos('build')}/{len(ok)}, check {pos('check')}, holdout {pos('holdout')}")
    for k in GRID:
        avg = defaultdict(list)
        for p, r in ok:
            avg[p[k]].append(r["build"]["pts"])
        print(f"  {k}: " + ", ".join(f"{v} {sum(x) / len(x):+.1f}pt" for v, x in avg.items()))


if __name__ == "__main__":
    main()
