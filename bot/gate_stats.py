"""Golden gate statistics: after the first touch of +/-38.2% ATR, how often does price reach
+/-61.8% that session, and what does a fixed trade (entry at 38.2, stop at 23.6 trigger) earn?

    python3 -m bot.gate_stats AAPL,MSFT 2026-03-09 2026-08-31
"""
import datetime as dt
import sys
from collections import defaultdict
from bot.backtest import SymbolData, wilder_atr
from bot.strategy import saty_levels

syms = sys.argv[1].split(",")
start, end = dt.date.fromisoformat(sys.argv[2]), dt.date.fromisoformat(sys.argv[3])
res = defaultdict(lambda: [0, 0, 0, 0, 0])   # touches, completes, win, loss, sum R
for s in syms:
    d = SymbolData("data", s)
    for day in sorted(d.m5):
        if not (start <= day <= end):
            continue
        prior = [r for r in d.daily if r[0] < day]
        if len(prior) < 20:
            continue
        lv = saty_levels(prior[-1][4], wilder_atr(prior))
        bars = [b for _, b in d.rth(day)]
        if not bars:
            continue
        for side in (1, -1):
            gate, goal, stop = lv[side * 0.382], lv[side * 0.618], lv[side * 0.236]
            hit = lambda b, x: (b.h >= x) if side == 1 else (b.l <= x)
            open_beyond = (bars[0].o >= gate) if side == 1 else (bars[0].o <= gate)
            if open_beyond:
                continue
            i = next((k for k, b in enumerate(bars) if hit(b, gate)), None)
            if i is None:
                continue
            r = res[side]
            r[0] += 1
            reward, risk = abs(goal - gate), abs(gate - stop)
            outcome = None
            for k, b in enumerate(bars[i:]):
                stop_hit = (b.l <= stop) if side == 1 else (b.h >= stop)
                if k == 0:
                    stop_hit = False  # the touch bar itself came from the trigger side
                if stop_hit:
                    outcome = -1.0
                    break
                if hit(b, goal):
                    outcome = reward / risk
                    break
            if any(hit(b, goal) for b in bars[i:]):
                r[1] += 1
            if outcome is None:  # flat at the close
                last = bars[-1].c
                outcome = side * (last - gate) / risk
            r[2 if outcome > 0 else 3] += 1
            r[4] += outcome
for side, (n, c, w, l, sr) in res.items():
    print(f"{'call' if side == 1 else 'put'} side: {n} gate touches, reached 61.8% {c / n:.0%}, "
          f"fixed trade (entry 38.2, stop 23.6, target 61.8, else close): win {w / n:.0%}, avg {sr / n:+.2f}R")


def next_level_stats(syms, start, end):
    """After price first reaches an ATR level (from below, same session), how often does it reach the next one up?"""
    fibs = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.236]
    hits = {f: [0, 0] for f in fibs[:-1]}
    for s in syms:
        d = SymbolData("data", s)
        for day in sorted(d.m5):
            if not (start <= day <= end):
                continue
            prior = [r for r in d.daily if r[0] < day]
            if len(prior) < 20:
                continue
            lv = saty_levels(prior[-1][4], wilder_atr(prior))
            bars = [b for _, b in d.rth(day)]
            if not bars:
                continue
            for a, b in zip(fibs, fibs[1:]):
                if bars[0].o >= lv[a]:
                    continue
                i = next((k for k, x in enumerate(bars) if x.h >= lv[a]), None)
                if i is None:
                    continue
                hits[a][0] += 1
                hits[a][1] += any(x.h >= lv[b] for x in bars[i:])
    for a, b in zip(fibs, fibs[1:]):
        n, c = hits[a]
        print(f"reached {a:+.1%} -> {b:+.1%}: {c}/{n} = {c / max(n, 1):.0%}")


next_level_stats(syms, start, end)
