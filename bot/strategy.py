"""Enrich-style setups as code. The same classes drive the backtest and the live engine.

Two layers:

  SymbolStrategy.on_5m_close(bar)  -> decides on the 5-minute close: arms an entry,
                                      cancels one, or signals a runner/level exit.
  Position / ArmedEntry            -> checked against every finer price update
                                      (1-minute bars in the backtest, quotes live):
                                      entry trigger, stop, first target.

Setups (long only - the Robinhood account can't short):

  BRH  Breakout -> Retest -> Hold -> Continuation of PMH / PDH / PWH
       (guide sections 8 "Setup 1" and 10 "Blue Zone"):
       1. a 5m bar closes decisively above the level on expanding volume (rvol)
       2. a later 5m bar dips back to the level on lighter volume and closes above it
       3. at that close: price above VWAP, 9 EMA caught up below the candles,
          daily trend up (prior close above daily 9 EMA), stronger than SPY today
       4. buy-stop one cent above the retest bar's high; stop under the retest low
  EMA  9 EMA pullback in a trend (guide "Setup 2"), optional:
       new high of day recently, pullback touches the rising 5m 9 EMA on lighter
       volume and closes above it; buy-stop above that bar.

  SATY Saty ATR Levels + Pivot Ribbon (from the Saty indicator code and his published
       setup names; rules provisional until checked against the video transcripts):
       levels = prior daily close +/- fib x daily ATR(14): 23.6% call/put trigger,
       38.2% / 50% / 61.8% ("golden gate") / 78.6% / 100% / 123.6% / 161.8% targets.
       Between the two triggers is the trigger box: no longs there.
       Ribbon = 8/21/34 EMA on 5m closes; bullish when close >= 8 >= 21 >= 34;
       conviction = 13 EMA above 48 EMA. Time Warp (optional) = the same ribbon on a
       higher timeframe built from the 5m bars must also be bullish.
       Range used (optional) = today's high - low as a share of the daily ATR; no new
       longs once most of the ATR is spent.
       Entry variants (Params.saty_entries):
         trigger   first 5m close above the call trigger
         gate      first 5m close above the 38.2% level (golden gate opens; aims at 61.8%)
         gate_touch   resting buy-stop at the 38.2% level while price sits between the
                   call trigger and the gate; stop under the call trigger. Golden gate
                   completion (38.2% -> 61.8% the same day) measured 51-57% on the
                   17-ticker list, Mar-Oct 2026.
         gate_retest  after the gate opened, a bar dips back to 38.2% and closes above it
         pullback  above the call trigger, a bar dips to the 21 EMA and closes above it
         flag      bull flag above the call trigger: a pole of >= flag_pole_atr 5m ATRs,
                   then 2-flag_max_bars bars drifting sideways/down on lighter volume,
                   holding the 21 EMA and retracing <= flag_retrace of the pole;
                   buy-stop above the flag high, stop under the flag low
         ivomy     the ribbon flips bullish (8 > 21 > 34 after not being) with a close
                   above it, above the call trigger
       Targets are ATR levels: T1 = nearest level >= saty_min_r R above the entry (sell
       trim_frac), T2 = the next level above T1 (sell the rest); between them the runner
       exits on a 5m close below the saty_runner_ema ribbon EMA.
       saty_flag_confirm: the other variants only fire when a bull flag is also in place,
       and their buy-stop moves to the flag high.
       Bear flag (long-only account): a bear flag breaking down closes an open long and
       blocks new longs for the rest of that flag's move.

  SATYD Saty's day-trade checklist, from his videos "ATR Levels Overview", "Pivot Ribbon
       Tutorial" and "Day Trading with ATR Levels and Ripster EMA Clouds". Trend and
       entries on 10-minute bars built from the 5m bars (he uses the 10m for trend, and
       enters on it unless price moves fast):
       essential  - trend: 10m close above the 34/50 EMA cloud (Ripster's golden rule)
                  - a 10m close above the call trigger (+23.6% ATR)
                  - less than 70% of the daily ATR covered (today's high - low)
       ideal      - "super bullish": close > 5 > 8 > 12 > 21 > 34 > 50 EMA (sd_stacked)
       rules      - no trades in the first 10 minutes; never chase: wait for a pullback
                    to the 8/9 EMA (or the 21, the pivot) and enter as it bounces
                    (buy-stop above the pullback bar)
       stop       - under the 34/50 cloud, the call trigger, or the pullback bar (sd_stop)
       targets    - scale out at the mid-range (61.8% ATR), runners to the full ATR;
                    runners exit on a 10m close below the sd_runner EMA (21 or the 5/12
                    cloud); a 10m close below the 34/50 cloud before T1 ends the trade

  SATYG Saty's Golden Gate, from "The Golden Gate Strat" and his Time Warp videos:
       once price reaches +38.2% ATR (the golden gate), ~60% of the time it reaches 61.8%
       (the golden fib) that day; any ATR level reached leads to the next >70% of the time.
       Trend = 10m ribbon 8 > 13 > 21 > 34 > 48; entries on the lower timeframe (5m here,
       3m in the videos) on a pullback into the 8-13 zone (21/34/48 also "viable") that
       holds, above the previous close; never chase. Stop under the 48 (or the trigger).
       Scale out at 50% and 61.8%; hold while no 10m candle closes below the 13 EMA.

Exits (guide sections 4 and 15):
  - hard stop
  - first target at +t1_r R: sell half, stop to break-even ("never let a green trade go red")
  - before the first target: a 5m close back below the broken level ends the trade
  - runner: exit on a 5m close below the 9 EMA ("9 EMA lost on the timeframe you trade")
  - everything closed at flatten time
"""
from dataclasses import dataclass, field
from datetime import time
from typing import Optional


@dataclass
class Params:
    levels: tuple = ("pmh", "pdh", "pwh")
    setups: tuple = ("BRH",)
    break_min_rvol: float = 1.2        # break bar volume vs same-time 10-day average
    break_close_frac: float = 0.5      # break bar closes in the top half of its range
    retest_max_bars: int = 12          # retest must come within 60 min of the break
    retest_tol_atr: float = 0.25       # how close the retest low must come to the level
    arm_bars: int = 2                  # buy-stop stays live for 10 minutes
    ema_catchup_atr: float = 1.0       # close - 9EMA must be within this many ATRs
    max_extension_atr: float = 1.0     # trigger at most this far above the level (no chasing)
    max_risk_atr: float = 1.5
    min_risk_pct: float = 0.001
    stop_buffer_atr: float = 0.05
    # "close": the guide's invalidation - a 5m close back below the level ends the trade;
    #          the hard stop sits hard_stop_atr below the retest low as disaster protection.
    # "tight": hard stop just under the retest low (stop_buffer_atr).
    stop_mode: str = "close"
    hard_stop_atr: float = 0.75
    t1_r: float = 1.0
    trim_frac: float = 0.5
    require_daily_trend: bool = True   # prior daily close above daily 9 EMA
    require_vwap: bool = True
    require_rs: bool = True            # % change from open beats SPY's
    require_market: str = "none"       # "vwap": SPY above its VWAP; "ema": SPY above its 5m 9 EMA; "both"
    require_daily_ema50: bool = False  # prior daily close above the daily 50 EMA too
    retest_vol_frac: float = 1.0       # retest bar volume < this x break bar volume
    require_leader: bool = False       # guide 8b: above daily 9/50/200 EMA, within 10% of 60-day high, beats SPY over 20 days
    ema_at_level_atr: Optional[float] = None  # guide Step 3: 9 EMA has risen to within this many ATRs of the level
    level_exit: bool = True            # 5m close back below the level before T1 = out
    runner_exit_ema: bool = True
    entry_start: time = time(9, 40)
    last_entry: time = time(15, 0)
    flatten: time = time(15, 55)
    max_trades_per_symbol: int = 2
    # SATYD: Saty day-trade checklist on 10m bars
    sd_max_range: float = 0.7          # max share of the daily ATR covered at entry
    sd_stacked: bool = False           # require close > 5 > 8 > 12 > 21 > 34 > 50
    sd_above_512: bool = False         # also above the 5/12 cloud ("extra bullish")
    sd_pullback_ema: int = 9           # pullback must touch this 10m EMA (8, 9 or 21)
    sd_touch_atr: float = 0.15         # "touch" = low within this many 10m ATRs above the EMA
    sd_stop: str = "cloud"             # "cloud" (under 34/50), "trigger", "bar"
    sd_stop_atr: float = 0.1           # buffer under the stop reference, in 10m ATRs
    sd_t1: float = 0.618               # mid-range target, share of ATR
    sd_t2: Optional[float] = 1.0       # full-range target (None: runner only)
    sd_runner: int = 21                # runner exit EMA on 10m closes (12 = 5/12 cloud)
    sd_min_room_r: float = 0.5         # T1 at least this many R above the entry
    # SATYG: Saty's Golden Gate (videos "The Golden Gate Strat", "Conviction Arrows and Time
    # Warp", "Scalping and Day Trading SPX and SPY with Time Warp")
    sg_pullback_ema: int = 8           # pullback reaches the 10m 8 EMA (into the 8-13 zone) or deeper
    sg_hold_ema: int = 13              # and the 5m bar closes back above this 10m EMA
    sg_stop: str = "48"                # stop under this 10m EMA ("21", "34", "48") or "trigger"
    sg_exit_ema: int = 13              # exit on a 10m close below this EMA (13, or 21)
    sg_t1: float = 0.5                 # first scale-out level (share of ATR)
    sg_t2: Optional[float] = 0.618     # golden fib: the rest (None: hold until the exit EMA)
    sg_entry: str = "pullback"         # "pullback" (into the 8-13 zone after the gate) or "touch"
                                       # (buy-stop at the gate itself, stop under the call trigger)
    sg_confirm: str = "stacked"        # touch entries: "stacked" ribbon, "bull" (close > 8 > 21 > 34),
                                       # "above48" (close and 13 EMA above the 48), "none"
    sg_fail_ema: Optional[int] = None  # before T1: exit on a 10m close below this EMA (e.g. 48)
    sg_exit_after_t1: bool = False     # the 10m-close exit only once T1 is taken (stop before that)
    sg_above_pdc: bool = True          # bias: above the previous close
    sg_max_range: Optional[float] = None
    sg_min_room_r: float = 0.5
    slippage_pct: float = 0.0003       # per fill
    # Inside the bar an entry fills on: "worst" assumes its low came after the fill;
    # "ohlc" assumes open-low-high-close for a green bar (the low came first) and
    # open-high-low-close for a red one.
    entry_bar_path: str = "ohlc"         # matched 1m execution exactly on Oct 2026
    # Saty ATR Levels + Pivot Ribbon
    saty_entries: tuple = ("trigger", "gate", "pullback")
    saty_conviction: bool = True       # 13 EMA above 48 EMA on 5m
    saty_min_r: float = 1.0            # first target = nearest ATR level >= this many R away
    saty_stop_atr: float = 0.1         # stop buffer under the bar low / level, in 5m ATRs
    saty_runner_ema: int = 21          # runner exits on a 5m close below this ribbon EMA
    saty_rs: bool = False              # also require beating SPY since the open
    saty_t2: bool = True               # second target at the next ATR level above T1
    saty_htf_bars: int = 0             # Time Warp: ribbon on N x 5m bars must be bullish (0 = off)
    saty_max_range: Optional[float] = None  # no entries once (HOD - LOD) / daily ATR >= this
    saty_bear_flag_exit: bool = True
    saty_stop_only: tuple = ("gate_touch",)  # variants that exit before T1 only at the stop
    saty_flag_confirm: bool = False    # trigger/gate/gate_retest/pullback/ivomy also need a bull flag
    flag_pole_atr: float = 2.0         # pole height in 5m ATRs
    flag_pole_bars: int = 6            # pole made within this many bars
    flag_max_bars: int = 6             # flag length 2..this
    flag_retrace: float = 0.5          # flag may give back at most this share of the pole


@dataclass
class DayContext:
    """Everything known before the open."""
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    pdc: Optional[float] = None
    pwh: Optional[float] = None
    pwl: Optional[float] = None
    pmh: Optional[float] = None
    pml: Optional[float] = None
    daily_atr: Optional[float] = None   # Wilder ATR(14) of daily bars through the prior session
    daily_ema9: Optional[float] = None
    daily_ema50: Optional[float] = None
    daily_ema200: Optional[float] = None
    high60: Optional[float] = None      # highest daily high of the last 60 sessions
    ret20: Optional[float] = None       # 20-session return
    spy_ret20: Optional[float] = None
    slot_volume: dict = field(default_factory=dict)   # "HH:MM" -> average 5m volume


@dataclass
class Bar:
    ts: object          # timezone-aware datetime (bar start)
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class ArmedEntry:
    symbol: str
    setup: str
    level_name: str
    level: float
    trigger: float
    stop: float
    expires_after: int      # 5m bar index after which it lapses
    armed_at: object
    target: Optional[float] = None   # fixed first target (SATY); otherwise t1_r R
    target2: Optional[float] = None  # second target: the rest is sold there


@dataclass
class Position:
    symbol: str
    setup: str
    level_name: str
    level: float
    entry: float
    stop: float
    risk: float
    t1: float
    opened_at: object
    t2: Optional[float] = None      # sell the rest here
    remaining: float = 1.0          # fraction of the original size still open
    trimmed: bool = False
    realized_r: float = 0.0
    fills: list = field(default_factory=list)
    high: float = 0.0               # best price seen while open (for MFE)
    low: float = float("inf")       # worst price seen while open (for MAE)

    def mark(self, bar):
        self.high = max(self.high, bar.h)
        self.low = min(self.low, bar.l)

    def exit(self, price, frac, ts, reason):
        self.realized_r += frac * (price - self.entry) / self.risk
        self.remaining -= frac
        self.fills.append((ts, "sell", round(price, 4), frac, reason))


class EMA:
    def __init__(self, period, value=None):
        self.k = 2 / (period + 1)
        self.value = value

    def update(self, x):
        self.value = x if self.value is None else self.value + self.k * (x - self.value)
        return self.value


class ATR:
    def __init__(self, period=14):
        self.period, self.value, self.prev_close, self.n = period, None, None, 0

    def update(self, bar):
        tr = bar.h - bar.l if self.prev_close is None else max(
            bar.h - bar.l, abs(bar.h - self.prev_close), abs(bar.l - self.prev_close))
        self.prev_close = bar.c
        self.n += 1
        if self.value is None:
            self.value = tr
        else:
            self.value += (tr - self.value) / min(self.n, self.period)
        return self.value


class SymbolStrategy:
    """5-minute decision logic for one symbol. EMA/ATR carry over between days;
    call start_day() before each session's first bar."""

    def __init__(self, symbol, params: Params):
        self.symbol, self.p = symbol, params
        self.ema9 = EMA(9)
        self.ribbon = {n: EMA(n) for n in (8, 13, 21, 34, 48)}
        self.htf_ribbon = {n: EMA(n) for n in (8, 21, 34)}
        self.e10 = {n: EMA(n) for n in (5, 8, 9, 12, 13, 21, 34, 48, 50)}
        self.e10v = {}
        self.atr10 = ATR(14)
        self.prev_bull = False
        self.atr = ATR(14)
        self.ctx = DayContext()

    # -- session ------------------------------------------------------------
    def start_day(self, ctx: DayContext):
        self.ctx = ctx
        self.idx = -1
        self.cum_pv = self.cum_v = 0.0
        self.vwap = None
        self.day_open = None
        self.hod = None
        self.hod_idx = -1
        self.ema_hist = []
        self.vol_hist = []
        self.trades_today = 0
        self.armed: Optional[ArmedEntry] = None
        self.position: Optional[Position] = None
        self.levels = {}
        for name in self.p.levels:
            value = getattr(ctx, name)
            if value is None:
                continue
            # Levels within 0.2% of each other are one zone; keep the higher one.
            if any(abs(value - other["level"]) / value < 0.002 for other in self.levels.values()):
                continue
            self.levels[name] = {"level": value, "state": "new", "break_idx": None,
                                 "break_vol": None, "done": False}
        self.saty = saty_levels(ctx.pdc, ctx.daily_atr)
        self.saty_used = set()     # entry variants already taken today
        self.bars = []             # today's 5m bars, for flags
        self.lod = None
        self.gate_open = False
        self.bear_block = None     # broken bear flag's high: no longs until a close above it
        self.b10 = None            # 10m bar being built
        self.sd_triggered = False  # a 10m close above the call trigger happened today
        self.lod = None
    # -- 5m close -----------------------------------------------------------
    def on_5m_close(self, bar: Bar, slot: str, close_time: time, spy_change: Optional[float], market=None):
        """Call once per completed 5m regular-session bar.

        slot is the bar's start time "HH:MM" ET (for relative volume), close_time
        its end time (for the entry window), spy_change SPY's % change from today's
        open at the same bar. Returns actions: ("arm", ArmedEntry),
        ("cancel", reason), ("exit", reason) for the open position.
        market: {"above_vwap": bool, "above_ema9": bool} for SPY at the same bar."""
        self.market = market or {}
        p = self.p
        self.idx += 1
        if self.day_open is None:
            self.day_open = bar.o
        typical = (bar.h + bar.l + bar.c) / 3
        self.cum_pv += typical * bar.v
        self.cum_v += bar.v
        self.vwap = self.cum_pv / self.cum_v if self.cum_v else bar.c
        ema = self.ema9.update(bar.c)
        rib = {n: e.update(bar.c) for n, e in self.ribbon.items()}
        self.bars.append(bar)
        self.lod = bar.l if self.lod is None else min(self.lod, bar.l)
        self._update_htf(bar)
        bull = bar.c >= rib[8] >= rib[21] >= rib[34]
        self.flipped, self.prev_bull = bull and not self.prev_bull, bull
        mid = self.saty.get(0.382)
        self.gate_opened_now = mid is not None and not self.gate_open and bar.c > mid
        self.gate_open = self.gate_open or self.gate_opened_now
        atr = self.atr.update(bar)
        self.ema_hist.append(ema)
        self.vol_hist.append(bar.v)
        if self.hod is None or bar.h > self.hod:
            self.hod, self.hod_idx = bar.h, self.idx
        slot_avg = self.ctx.slot_volume.get(slot)
        rvol = bar.v / slot_avg if slot_avg else None
        actions = []

        # Position management on the 5m close
        pos = self.position
        if pos is not None:
            if p.level_exit and not pos.trimmed and pos.setup == "BRH" and bar.c < pos.level * (1 - 0.001):
                actions.append(("exit", "closed back below level"))
            elif p.runner_exit_ema and pos.trimmed and pos.setup in ("BRH", "EMA") and bar.c < ema:
                actions.append(("exit", "5m close below 9 EMA"))
            elif pos.setup == "EMA" and not pos.trimmed and bar.c < ema - 0.1 * atr:
                actions.append(("exit", "lost the 9 EMA"))
            elif (pos.setup == "SATY" and not pos.trimmed and pos.level_name not in p.saty_stop_only
                  and (bar.c < rib[34] or bar.c < pos.level * (1 - 0.001))):
                actions.append(("exit", "lost the ribbon / level"))
            elif pos.setup == "SATY" and pos.trimmed and p.saty_runner_ema and bar.c < rib[p.saty_runner_ema]:
                actions.append(("exit", f"5m close below {p.saty_runner_ema} EMA"))
        bear = find_flag(self.bars[:-1], atr, p, bull=False) if "SATY" in p.setups else None
        if bear and bar.c < bear[0]:
            self.bear_block = bear[1]
            if pos is not None and pos.setup == "SATY" and p.saty_bear_flag_exit and not any(a[0] == "exit" for a in actions):
                actions.append(("exit", "bear flag breakdown"))
        elif self.bear_block is not None and bar.c > self.bear_block:
            self.bear_block = None

        # An armed entry that wasn't triggered lapses, or is cancelled by a close below the level
        if self.armed is not None:
            a = self.armed
            if bar.c < a.level * (1 - 0.001) and a.setup == "BRH":
                actions.append(("cancel", "closed below level"))
                self.armed = None
                self.levels[a.level_name]["state"] = "below"
            elif self.idx > a.expires_after:
                actions.append(("cancel", "not triggered in time"))
                self.armed = None
                if a.setup == "BRH":
                    self.levels[a.level_name]["state"] = "broken"

        # Level state machines
        for name, lv in self.levels.items():
            if lv["done"]:
                continue
            level = lv["level"]
            if lv["state"] == "new":
                # Opened above the level: not a breakout until it closes below again.
                lv["state"] = "above" if bar.o > level else "below"
            if lv["state"] == "above":
                if bar.c < level:
                    lv["state"] = "below"
                continue
            if lv["state"] == "below":
                rng = bar.h - bar.l
                decisive = rng > 0 and (bar.c - bar.l) / rng >= p.break_close_frac
                if bar.c > level * (1 + 0.0005) and decisive and rvol is not None and rvol >= p.break_min_rvol:
                    lv.update(state="broken", break_idx=self.idx, break_vol=bar.v)
                continue
            if lv["state"] == "broken":
                if bar.c < level * (1 - 0.001):
                    lv["state"] = "below"
                    continue
                if self.idx - lv["break_idx"] > p.retest_max_bars:
                    lv["state"] = "expired"
                    continue
                if self.idx == lv["break_idx"]:
                    continue
                tol = max(p.retest_tol_atr * atr, level * 0.001)
                is_retest = bar.l <= level + tol and bar.c >= level and bar.v < p.retest_vol_frac * lv["break_vol"]
                if is_retest and p.ema_at_level_atr is not None and ema < level - p.ema_at_level_atr * atr:
                    is_retest = False
                if is_retest and "BRH" in p.setups and self.armed is None and self.position is None:
                    armed = self._try_arm("BRH", name, level, bar, close_time, ema, atr, spy_change,
                                          stop_base=min(bar.l, level))
                    if armed:
                        lv["state"] = "armed"
                        actions.append(("arm", armed))

        if "EMA" in p.setups and self.armed is None and self.position is None:
            armed = self._ema_pullback(bar, close_time, ema, atr, spy_change)
            if armed:
                actions.append(("arm", armed))
        if "SATY" in p.setups and self.armed is None and self.position is None and self.saty:
            armed = self._saty(bar, close_time, rib, atr, spy_change)
            if armed:
                actions.append(("arm", armed))
        if ("SATYD" in p.setups or "SATYG" in p.setups) and self.saty:
            b10 = self._update_10m(bar)
            if "SATYD" in p.setups:
                actions += self._saty_day(bar, close_time, b10)
            if "SATYG" in p.setups:
                actions += self._saty_gate(bar, close_time, b10, atr)
        self.prev_close = bar.c
        return actions

    def _saty_gate(self, bar, close_time, b10, atr):
        """Golden Gate, as in Saty's videos: 10m ribbon trend, 5m entries (Time Warp)."""
        p, lv, e = self.p, self.saty, self.e10v
        if not e or e[48] is None:
            return []
        actions = []
        pos = self.position
        if (b10 is not None and pos is not None and pos.setup == "SATYG" and b10.c < e[p.sg_exit_ema]
                and (pos.trimmed or not p.sg_exit_after_t1)):
            actions.append(("exit", f"10m close below the {p.sg_exit_ema} EMA"))
        elif (b10 is not None and pos is not None and pos.setup == "SATYG" and not pos.trimmed
              and p.sg_fail_ema and b10.c < e[p.sg_fail_ema]):
            actions.append(("exit", f"ribbon failed: 10m close below the {p.sg_fail_ema} EMA"))
        if (self.armed is not None or pos is not None or self.trades_today >= p.max_trades_per_symbol
                or not (p.entry_start <= close_time < p.last_entry)):
            return actions
        gate, t1, t2 = lv[0.382], lv[p.sg_t1], lv[p.sg_t2] if p.sg_t2 else None
        if p.sg_entry == "touch":
            return actions + self._gate_touch(bar, e, atr, gate, t1, t2)
        if self.hod < gate or bar.c <= lv[0.236] or bar.c >= t1:      # gate not opened yet / lost trigger / no room
            return actions
        if p.sg_above_pdc and bar.c <= self.ctx.pdc:
            return actions
        if not (e[8] > e[13] > e[21] > e[34] > e[48]):                # 10m ribbon stacked bullish
            return actions
        if p.sg_max_range is not None and (self.hod - self.lod) >= p.sg_max_range * self.ctx.daily_atr:
            return actions
        tol = 0.1 * atr
        pulled = bar.l <= e[p.sg_pullback_ema] + tol and bar.l > e[48] - tol
        if not (pulled and bar.c > e[p.sg_hold_ema]):
            return actions
        entry = round(bar.h + 0.01, 2)
        ref = lv[0.236] if p.sg_stop == "trigger" else e[int(p.sg_stop)]
        stop = round(min(bar.l, ref) - max(0.01, p.saty_stop_atr * atr), 2)
        risk = entry - stop
        if risk <= 0 or risk < p.min_risk_pct * entry or t1 - entry < p.sg_min_room_r * risk:
            return actions
        self.armed = ArmedEntry(self.symbol, "SATYG", "golden_gate", gate, entry, stop,
                                expires_after=self.idx + p.arm_bars, armed_at=bar.ts, target=t1, target2=t2)
        actions.append(("arm", self.armed))
        return actions

    def _gate_touch(self, bar, e, atr, gate, t1, t2):
        """Buy-stop at the golden gate before its first touch today, out of the trigger box,
        with the 10m ribbon confirming; stop under the call trigger."""
        p, lv = self.p, self.saty
        trigger = lv[0.236]
        if self.hod >= gate or not (trigger < bar.c < gate):
            return []
        if p.sg_above_pdc and bar.c <= self.ctx.pdc:
            return []
        c = p.sg_confirm
        if c == "stacked" and not (e[8] > e[13] > e[21] > e[34] > e[48]):
            return []
        if c == "bull" and not (bar.c > e[8] > e[21] > e[34]):
            return []
        if c == "above48" and not (bar.c > e[48] and e[13] > e[48]):
            return []
        if p.sg_max_range is not None and (self.hod - self.lod) >= p.sg_max_range * self.ctx.daily_atr:
            return []
        entry = round(gate + 0.01, 2)
        stop = round(trigger - max(0.01, p.saty_stop_atr * atr), 2)
        risk = entry - stop
        if risk <= 0 or risk < p.min_risk_pct * entry:
            return []
        self.armed = ArmedEntry(self.symbol, "SATYG", "gate_touch", gate, entry, stop,
                                expires_after=self.idx + 1, armed_at=bar.ts, target=t1, target2=t2)
        return [("arm", self.armed)]

    def _update_10m(self, bar):
        """Build 10m bars from pairs of 5m bars from the open. Returns the 10m bar that
        closed with this 5m bar, or None; self.e10v / self.atr10v hold the last 10m values."""
        if self.idx % 2 == 0:
            self.b10 = Bar(bar.ts, bar.o, bar.h, bar.l, bar.c, bar.v)
            return None
        b = self.b10 or bar
        b10 = Bar(b.ts, b.o, max(b.h, bar.h), min(b.l, bar.l), bar.c, b.v + bar.v)
        self.e10v = {n: x.update(b10.c) for n, x in self.e10.items()}
        self.atr10v = self.atr10.update(b10)
        return b10

    def _saty_day(self, bar, close_time, b10):
        """Act on each 10m close."""
        p, lv = self.p, self.saty
        if b10 is None:
            return []
        e, atr10 = self.e10v, self.atr10v
        actions = []
        cloud_lo = min(e[34], e[50])
        pos = self.position
        if pos is not None and pos.setup == "SATYD":
            if not pos.trimmed and b10.c < cloud_lo:
                actions.append(("exit", "10m close below the 34/50 cloud"))
            elif pos.trimmed and b10.c < (min(e[5], e[12]) if p.sd_runner == 12 else e[p.sd_runner]):
                actions.append(("exit", f"10m close below runner EMA {p.sd_runner}"))
        trigger = lv[0.236]
        if b10.c > trigger:
            self.sd_triggered = True
        if (self.armed is not None or self.position is not None or not self.sd_triggered
                or not (p.entry_start <= close_time < p.last_entry) or self.trades_today >= p.max_trades_per_symbol):
            return actions
        # essentials
        if b10.c <= max(e[34], e[50]) or b10.c <= trigger:
            return actions
        if (self.hod - self.lod) >= p.sd_max_range * self.ctx.daily_atr:
            return actions
        if p.sd_above_512 and b10.c <= max(e[5], e[12]):
            return actions
        if p.sd_stacked and not (b10.c > e[5] > e[8] > e[12] > e[21] > e[34] > e[50]):
            return actions
        # pullback to the 8/9 (or 21) EMA that holds
        ref = e[p.sd_pullback_ema]
        if not (b10.l <= ref + p.sd_touch_atr * atr10 and b10.c > ref and b10.c > e[21]):
            return actions
        entry = round(b10.h + 0.01, 2)
        base = {"cloud": cloud_lo, "trigger": trigger, "bar": b10.l}[p.sd_stop]
        stop = round(min(base, b10.l) - max(0.01, p.sd_stop_atr * atr10), 2)
        risk = entry - stop
        t1 = self.ctx.pdc + p.sd_t1 * self.ctx.daily_atr
        t2 = self.ctx.pdc + p.sd_t2 * self.ctx.daily_atr if p.sd_t2 else None
        if risk <= 0 or risk < p.min_risk_pct * entry or t1 - entry < p.sd_min_room_r * risk:
            return actions
        self.armed = ArmedEntry(self.symbol, "SATYD", "saty_day", trigger, entry, stop,
                                expires_after=self.idx + 2, armed_at=bar.ts, target=t1, target2=t2)
        actions.append(("arm", self.armed))
        return actions

    def _update_htf(self, bar):
        """Time Warp: fold 5m bars into N x 5m bars from the open; ribbon on their closes."""
        n = self.p.saty_htf_bars
        if not n:
            return
        if self.idx % n == n - 1:
            for e in self.htf_ribbon.values():
                e.update(bar.c)

    def _htf_bull(self):
        e = self.htf_ribbon
        return e[8].value is not None and e[8].value >= e[21].value >= e[34].value

    def _saty(self, bar, local_time, rib, atr, spy_change):
        p, lv = self.p, self.saty
        bull, flipped, opened_now = self.prev_bull, self.flipped, self.gate_opened_now
        prev = getattr(self, "prev_close", None)
        prev = bar.o if prev is None or self.idx == 0 else prev
        trigger, mid = lv[0.236], lv[0.382]
        if not (p.entry_start <= local_time < p.last_entry) or self.trades_today >= p.max_trades_per_symbol:
            return None
        if not bull or bar.c <= trigger:            # bearish ribbon or inside the trigger box
            return None
        if p.saty_conviction and rib[13] <= rib[48]:
            return None
        if p.saty_htf_bars and not self._htf_bull():
            return None
        if p.saty_max_range is not None and (self.hod - self.lod) >= p.saty_max_range * self.ctx.daily_atr:
            return None
        if self.bear_block is not None:
            return None
        ctx = self.ctx
        if p.require_daily_trend and (ctx.daily_ema9 is None or ctx.pdc is None or ctx.pdc <= ctx.daily_ema9):
            return None
        if p.require_market in ("vwap", "both") and not self.market.get("above_vwap"):
            return None
        if p.require_market in ("ema", "both") and not self.market.get("above_ema9"):
            return None
        if p.saty_rs and (spy_change is None or bar.c / self.day_open - 1 <= spy_change):
            return None
        for variant in p.saty_entries:
            if variant in self.saty_used:
                continue
            entry = round(bar.h + 0.01, 2)
            if variant == "trigger" and prev <= trigger < bar.c and bar.c < mid:
                level, stop_base = trigger, min(bar.l, trigger)
            elif variant == "gate_touch" and not self.gate_open and bar.c < mid:
                entry, level, stop_base = round(mid + 0.01, 2), mid, trigger
            elif variant == "gate" and opened_now and prev <= mid and bar.c < lv[0.618]:
                level, stop_base = mid, min(bar.l, mid)
            elif (variant == "gate_retest" and self.gate_open and not opened_now and bar.c > mid
                  and bar.l <= mid + 0.1 * atr and bar.c < lv[0.618]):
                level, stop_base = mid, min(bar.l, mid)
            elif (variant == "pullback" and bar.l <= rib[21] + 0.1 * atr
                  and bar.c > rib[21] and bar.l > rib[34]):
                level, stop_base = trigger, min(bar.l, rib[34])
            elif variant == "flag":
                flag = find_flag(self.bars, atr, p, bull=True)
                if not flag or flag[1] < rib[21] - 0.1 * atr:
                    continue
                entry = round(flag[0] + 0.01, 2)
                level, stop_base = flag[1], flag[1]
            elif variant == "ivomy" and flipped and rib[8] > rib[21] > rib[34]:
                level, stop_base = min(rib[34], bar.l), min(bar.l, rib[34])
            else:
                continue
            if p.saty_flag_confirm and variant != "flag":
                flag = find_flag(self.bars, atr, p, bull=True)
                if not flag:
                    continue
                entry = round(max(entry, flag[0] + 0.01), 2)
            stop = round(stop_base - max(0.01, p.saty_stop_atr * atr), 2)
            risk = entry - stop
            level_risk = variant == "gate_touch"      # risk set by the ATR levels, not the 5m ATR
            if risk <= 0 or (risk > p.max_risk_atr * atr and not level_risk) or risk < p.min_risk_pct * entry:
                continue
            above = [v for k, v in sorted(lv.items()) if k > 0]
            target = next((v for v in above if v >= entry + p.saty_min_r * risk), None)
            if target is None:
                continue
            target2 = next((v for v in above if v > target), None) if p.saty_t2 else None
            if variant not in ("flag", "gate_touch"):    # these re-arm every bar until filled
                self.saty_used.add(variant)
            self.armed = ArmedEntry(self.symbol, "SATY", variant, level, entry, stop,
                                    expires_after=self.idx + p.arm_bars, armed_at=bar.ts,
                                    target=target, target2=target2)
            return self.armed
        return None

    def _ema_pullback(self, bar, local_time, ema, atr, spy_change):
        if local_time < time(10, 0) or len(self.ema_hist) < 4 or len(self.vol_hist) < 4:
            return None
        rising = ema > self.ema_hist[-4]
        recent_high = self.idx - self.hod_idx <= 6 and self.hod_idx != self.idx
        touch = bar.l <= ema + 0.1 * atr and bar.c > ema
        lighter = bar.v < sum(self.vol_hist[-4:-1]) / 3
        if rising and recent_high and touch and lighter:
            return self._try_arm("EMA", "ema9", ema, bar, local_time, ema, atr, spy_change,
                                 stop_base=min(bar.l, ema))
        return None

    def _try_arm(self, setup, level_name, level, bar, local_time, ema, atr, spy_change, stop_base):
        p, ctx = self.p, self.ctx
        if not (p.entry_start <= local_time < p.last_entry):
            return None
        if self.trades_today >= p.max_trades_per_symbol:
            return None
        if p.require_daily_trend and (ctx.daily_ema9 is None or ctx.pdc is None or ctx.pdc <= ctx.daily_ema9):
            return None
        if p.require_leader and not is_leader(ctx):
            return None
        if p.require_daily_ema50 and (ctx.daily_ema50 is None or ctx.pdc <= ctx.daily_ema50):
            return None
        if p.require_market in ("vwap", "both") and not self.market.get("above_vwap"):
            return None
        if p.require_market in ("ema", "both") and not self.market.get("above_ema9"):
            return None
        if p.require_vwap and bar.c <= self.vwap:
            return None
        if not (ema <= bar.c and bar.c - ema <= p.ema_catchup_atr * atr):
            return None
        if p.require_rs:
            if spy_change is None:
                return None
            if bar.c / self.day_open - 1 <= spy_change:
                return None
        trigger = round(bar.h + 0.01, 2)
        buffer = p.hard_stop_atr if p.stop_mode == "close" else p.stop_buffer_atr
        stop = round(stop_base - max(0.01, buffer * atr), 2)
        risk = trigger - stop
        if setup == "BRH" and trigger - level > p.max_extension_atr * atr:
            return None
        if risk <= 0 or risk > p.max_risk_atr * atr or risk < p.min_risk_pct * trigger:
            return None
        self.armed = ArmedEntry(self.symbol, setup, level_name, level, trigger, stop,
                                expires_after=self.idx + p.arm_bars, armed_at=bar.ts)
        return self.armed

    # -- fills (called by whoever executes) ------------------------------------
    def entry_filled(self, price, ts):
        a = self.armed
        risk = price - a.stop
        t1 = a.target if a.target is not None and a.target > price else price + self.p.t1_r * risk
        self.position = Position(a.symbol, a.setup, a.level_name, a.level, price, a.stop, risk,
                                 t1=t1, t2=a.target2 if a.target2 and a.target2 > t1 else None, opened_at=ts,
                                 fills=[(ts, "buy", round(price, 4), 1.0, a.setup)])
        self.armed = None
        self.trades_today += 1
        if a.setup == "BRH":
            self.levels[a.level_name]["done"] = True
        if a.setup == "SATY" and a.level_name != "flag":
            self.saty_used.add(a.level_name)
        return self.position

    def position_closed(self):
        pos, self.position = self.position, None
        return pos


SATY_FIBS = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.236, 1.618)


def saty_levels(close, atr):
    """Saty ATR Levels, Day mode: {fib: price}, negative keys below the close."""
    if close is None or not atr:
        return {}
    out = {0.0: close}
    for f in SATY_FIBS:
        out[f], out[-f] = close + f * atr, close - f * atr
    return out


def find_flag(bars, atr, p: Params, bull=True):
    """A flag ending at the last bar: (flag high, flag low) for a bull flag,
    (flag low, flag high) for a bear flag - i.e. (breakout side, invalidation side).
    Pole: a move of >= flag_pole_atr x atr within flag_pole_bars bars into the pole end;
    flag: the 2..flag_max_bars bars after it stay inside the pole's last flag_retrace,
    don't make a new extreme, and trade on lighter average volume than the pole."""
    if not atr:
        return None
    sign = 1 if bull else -1
    for k in range(2, p.flag_max_bars + 1):
        if len(bars) < k + 2:
            break
        flag, pole_end = bars[-k:], bars[-k - 1]
        pole = bars[max(0, len(bars) - k - 1 - p.flag_pole_bars):len(bars) - k]
        if bull:
            top, base = pole_end.h, min(b.l for b in pole)
            height = top - base
            hi, lo = max(b.h for b in flag), min(b.l for b in flag)
            ok = hi <= top and lo >= top - p.flag_retrace * height
        else:
            top, base = pole_end.l, max(b.h for b in pole)
            height = base - top
            hi, lo = min(b.l for b in flag), max(b.h for b in flag)
            ok = hi >= top and lo <= top + p.flag_retrace * height
        if height < p.flag_pole_atr * atr or not ok:
            continue
        if sum(b.v for b in flag) / k >= sum(b.v for b in pole) / len(pole):
            continue
        # The pole end must be the pole's extreme
        if sign * (pole_end.h if bull else pole_end.l) < max(sign * (b.h if bull else b.l) for b in pole):
            continue
        return (hi, lo)
    return None


def is_leader(ctx: DayContext):
    needed = (ctx.pdc, ctx.daily_ema9, ctx.daily_ema50, ctx.daily_ema200, ctx.high60, ctx.ret20, ctx.spy_ret20)
    if any(v is None for v in needed):
        return False
    return (ctx.pdc > ctx.daily_ema9 and ctx.pdc > ctx.daily_ema50 and ctx.pdc > ctx.daily_ema200
            and ctx.pdc >= 0.9 * ctx.high60 and ctx.ret20 > ctx.spy_ret20)


def check_price(strategy: SymbolStrategy, bar: Bar, p: Params):
    """Simulated execution against one price bar (1m in the backtest).
    Conservative: inside a bar, the stop is assumed to hit before the target."""
    events = []
    slip = p.slippage_pct
    a = strategy.armed
    if a is not None and strategy.position is None and bar.h >= a.trigger:
        fill = max(a.trigger, bar.o) * (1 + slip)
        if fill - a.trigger > 0.5 * (a.trigger - a.stop):
            strategy.armed = None   # gapped too far through the trigger: don't chase
            events.append(("skip", "gapped past trigger"))
        else:
            strategy.entry_filled(fill, bar.ts)
            events.append(("entry", fill))
            if p.entry_bar_path == "ohlc" and bar.c >= bar.o:
                # Green bar: the low came before the fill; only the close is known after it.
                strategy.position.mark(Bar(bar.ts, fill, bar.h, min(fill, bar.c), bar.c, bar.v))
                return events
            # Same bar: only the part after the trigger is known; check the stop on the low.
    pos = strategy.position
    if pos is None:
        return events
    pos.mark(bar)
    if bar.l <= pos.stop:
        price = min(pos.stop, bar.o) * (1 - slip)
        pos.exit(price, pos.remaining, bar.ts, "stop" if not pos.trimmed else "break-even stop")
        events.append(("closed", strategy.position_closed()))
        return events
    if not pos.trimmed and bar.h >= pos.t1:
        price = max(pos.t1, bar.o) * (1 - slip)
        pos.exit(price, p.trim_frac, bar.ts, "first target")
        pos.trimmed = True
        pos.stop = max(pos.stop, pos.entry)
        events.append(("trim", price))
    if pos.trimmed and pos.t2 is not None and bar.h >= pos.t2:
        price = max(pos.t2, bar.o) * (1 - slip)
        pos.exit(price, pos.remaining, bar.ts, "second target")
        events.append(("closed", strategy.position_closed()))
    return events


def exit_at(strategy: SymbolStrategy, price, ts, reason, p: Params):
    pos = strategy.position
    pos.exit(price * (1 - p.slippage_pct), pos.remaining, ts, reason)
    return strategy.position_closed()
