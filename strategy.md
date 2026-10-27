# Desk strategy

Every agent reads this file. Edit it in plain English to change how the desk trades;
numbers such as EMA lengths and risk limits live in `desk/config.json`.

Long only: Robinhood does not let this account sell short, so bearish reads mean "stay out" or "exit".

## Chart and levels
- Timeframe: 5-minute bars, regular session.
- **VWAP**: session VWAP from the 9:30 ET open.
- **EMAs**: fast and slow EMA on 5-minute closes (`strategy.ema_fast` / `strategy.ema_slow`, default 9 / 20).
- **PDH / PDL**: high and low of the previous regular session (9:30-16:00 ET).
- **PMH / PML**: high and low of today's premarket (4:00-9:30 ET, extended-hours bars).
- A level "holds" when a 5-minute bar closes on the right side of it. Wicks through don't count.

## Bias filter (check first)
- **Bullish**: price above VWAP, fast EMA above slow EMA, both EMAs rising.
- **Neutral / chop**: price crossing VWAP back and forth, EMAs flat or tangled → no trade.
- **Bearish**: price below VWAP and fast EMA below slow EMA → no new longs; exit any long.

## Long setups

### A. VWAP / EMA pullback
1. Bullish bias.
2. Price pulls back to VWAP or the fast EMA (low of the bar touches or comes within ~0.1%).
3. The pullback bar closes back above VWAP / fast EMA (the hold).
4. **Entry**: stop_limit buy a few cents above the hold bar's high.
5. **Stop**: below the hold bar's low, or below VWAP if that is lower.
6. **Target**: the next level above (PMH, PDH, high of day). Needs at least `min_reward_risk` R to the target.

### B. Level break and retest (PMH / PDH)
1. A 5-minute bar closes above PMH or PDH with bullish bias.
2. A later bar pulls back to that level and closes above it (retest holds).
3. **Entry**: limit near the level or stop_limit above the retest bar's high.
4. **Stop**: below the level (below the retest bar's low).
5. **Target**: next level above; if none, 2R, then trail.

### C. Reclaim of PML / PDL (lower conviction)
1. Price loses PML or PDL, then a 5-minute bar closes back above it and above VWAP.
2. Entry above that bar's high, stop below the reclaim bar's low, target VWAP or the next level.
3. Rate confidence one point lower than A or B.

## Skip
- First 5 minutes after the open (the default windows start at 9:35).
- Price boxed between two levels less than 1R apart.
- Earnings today or after the close today, a trading halt, or a pending offering (news analyst flags these).
- Spread wider than 0.2% of price.

## Managing a position
- After the stop or target is set, don't widen the stop.
- At +1R, move the stop to breakeven.
- Exit at the target, or on a 5-minute close below the fast EMA once past +1R, or on a 5-minute close below VWAP at any time.
- Intraday trades are closed by `flatten_at` (default 15:50 ET).

## Swing (only when `risk.allow_swing` is true)
- Hold overnight only if the daily bar is closing above PDH and above VWAP, and there is no earnings date before the next session.
- Swing stop: below PDH or the day's VWAP, whichever is lower.
