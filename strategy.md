# Desk strategy: Saty ATR Levels + Pivot Ribbon

Every agent reads this file. Edit it in plain English to change how the desk trades;
numbers such as EMA lengths and risk limits live in `desk/config.json`.

This is Saty Mahajan's day-trading system as he teaches it in his videos (summary and sources
in `docs/saty-rules.md`). Long only: Robinhood does not let this account sell short, so his
bearish setups (put trigger breaks, Vomy) mean "stay out" or "exit".

> Backtests of these rules on 17 tickers (Mar–Oct 2026) came out about breakeven after costs.
> Run it in **paper mode** until it has proven itself on new data.

## Levels: Saty ATR Levels, day mode (scanner, once per day)
- ATR = Wilder's ATR(14) of daily bars through the previous session. PDC = previous close.
- Levels = PDC ± fib × ATR, fibs 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%, 123.6%:
  - **call trigger** +23.6% (put trigger −23.6%)
  - **golden gate** +38.2%, **mid** +50%, **golden fib** +61.8%, +78.6%, **full ATR** +100%
- **Trigger box**: between the put trigger and the call trigger. No longs inside it.
- **Bias**: above PDC is bullish for the day, below is bearish.
- **Range used** = (today's high − today's low) ÷ ATR. Under 70% there is room to move; from 70%
  the move is getting spent; from 90% don't start new longs.

## Trend: Pivot Ribbon on the 10-minute chart, extended hours on
- EMAs 8 / 13 / 21 / 34 / 48 on 10-minute bars **including pre- and post-market**.
- **Bullish**: 8 > 13 > 21 > 34 > 48 and the 10-minute close above the 48.
- **Super bullish**: close > 8 > 13 > 21 > 34 > 48, all rising.
- **Chop**: a thin ribbon that keeps folding, or price crossing the 21 back and forth. No trade.
- **Bearish**: 13 below 48 (conviction arrow down) or the ribbon stacked down. No longs; exit longs.
- The 21 is the pivot. The 8–13 zone is where to enter and where a trend holds. The 48 is where the
  trend has likely changed if it breaks.
- Conviction arrow = 13 EMA crossing the 48. Not a buy signal by itself: a pullback usually
  follows it.

## Entries: 3-minute candles, "Time Warp" (3-minute candles, 10-minute ribbon)
Build 3-minute candles from 1-minute bars from 9:30 ET (9:30, 9:33, ...). Use completed candles only.
Compare them with the 10-minute ribbon values.

**Never in the first 10 minutes** (entry windows start 9:40). **Never chase**: if price has run
away from the ribbon, wait for the pullback. There is always another trade.

### Setup A: Golden Gate (main setup)
1. Price has reached the golden gate (+38.2%) today, above PDC, out of the trigger box.
   (Saty's numbers: once 38.2% is hit, ~60% of days reach 61.8%; any level hit leads to the next
   >70% of the time. Our data, 17 tickers: 51–57% and 72–76%.)
2. 10-minute ribbon bullish (stacked).
3. Pullback: a 3-minute candle dips into the 8–13 zone of the 10-minute ribbon (the 21 is also
   fine), does not break the 48, and closes back above the 13. Or a 3-minute close back above the
   golden gate after a pullback that held the ribbon.
4. **Entry**: stop_limit buy a cent above that 3-minute candle's high.
5. **Stop**: under the pullback low, and under the 21 (or 34 / 48 if that's closer to a real
   level). Never above the call trigger.
6. **Targets**: the ATR levels above: 50%, 61.8% (golden fib), 78.6%, full ATR.

### Setup B: Trigger break (day-trade checklist)
Essentials, all required:
1. Bullish trend: price above the 10-minute 34/48 EMAs (Saty uses Ripster's 34/50 cloud;
   above the 8/13 too is "extra bullish").
2. A 10-minute close above the call trigger.
3. Range used under 70%.
Ideal: super bullish ribbon. Then wait for the pullback to the 8–13 zone as in A.3–A.5.
First target 50% / 61.8%, then the next levels.

### Setup C: iVomy (reversal back up; lower conviction, confidence one point lower)
A downtrend meets support at a level (put trigger, PDC). Price breaks back above the 8 and 13,
tests the 48, then pulls back to the 13 and holds: enter on that retest. Confirmed once price
breaks and holds above the 48. Targets: PDC, call trigger, golden gate.

## Skip
- Earnings today or after the close today, a trading halt, or a pending offering.
- Spread wider than 0.2% of price.
- Range used 90% or more, or price already at or above the full ATR.
- The next ATR level is less than `min_reward_risk` R away from the entry.
- PDC or a trigger sits between the entry and the first target with the ribbon flat (chop).

## Managing a position (Saty: "trade like an algo")
- Before you enter, the plan has the entry, the stop and the target levels. Don't widen the stop.
- **Scale out at the ATR levels**: about a third at the first level hit (50% or 61.8%), a third at
  the next, and let the last third run toward the full ATR. After the first scale-out, move the
  stop to breakeven. Once price is through PDC or a level, the stop can move up under that level.
- **Hold** while no 10-minute candle closes below the 13 EMA. Exit the rest on a 10-minute close
  below the 13 (at the latest below the 21), or on a lower low after a higher high (the structure
  shifts).
- Exit everything on a 10-minute close below the 48, or a conviction arrow down.
- Intraday trades are closed by `flatten_at` (default 15:50 ET).

## Sizing (Saty: risk a fixed R, size from the technical stop)
- shares = `risk_per_trade_usd` ÷ (entry − stop), capped by `max_position_usd`. Never size up
  after wins. Daily loss cap `max_daily_loss_usd`.

## Swing (only when `risk.allow_swing` is true)
- Hold overnight only if the daily close is above the call trigger and the 10-minute ribbon is
  stacked bullish into the close, with no earnings before the next session. Stop under PDC.
