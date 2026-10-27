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
    slippage_pct: float = 0.0003       # per fill


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
            elif p.runner_exit_ema and pos.trimmed and bar.c < ema:
                actions.append(("exit", "5m close below 9 EMA"))
            elif pos.setup == "EMA" and not pos.trimmed and bar.c < ema - 0.1 * atr:
                actions.append(("exit", "lost the 9 EMA"))

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
        return actions

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
        self.position = Position(a.symbol, a.setup, a.level_name, a.level, price, a.stop, risk,
                                 t1=price + self.p.t1_r * risk, opened_at=ts,
                                 fills=[(ts, "buy", round(price, 4), 1.0, a.setup)])
        self.armed = None
        self.trades_today += 1
        if a.setup == "BRH":
            self.levels[a.level_name]["done"] = True
        return self.position

    def position_closed(self):
        pos, self.position = self.position, None
        return pos


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
    return events


def exit_at(strategy: SymbolStrategy, price, ts, reason, p: Params):
    pos = strategy.position
    pos.exit(price * (1 - p.slippage_pct), pos.remaining, ts, reason)
    return strategy.position_closed()
