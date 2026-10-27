# Saty Mahajan's rules, from his videos

Summarised from the transcripts of 11 videos (Oct 2026). The transcripts themselves aren't
in the repo. Each rule shows where it is coded: `SATYD` in `bot/strategy.py`.

Videos:
- **04** ATR Levels Overview and Tutorial
- **05** Pivot Ribbon Indicator Tutorial
- **06** Day Trading with Saty ATR Levels and Ripster EMA Clouds (setups, entries, exits)
- **08** Precision Levels, Extensions, and Modes
- **09** Scalping and Day Trading SPX and SPY with Time Warp
- **10** Making a SPY Day Trade Plan
- **11** The "Vomy" Setup
- **12** Bias Candles Quick Start
- **13** The Golden Gate Strat
- **CA** Conviction Arrows and Time Warp
- (07, on choosing option strikes, isn't relevant to stock trades)

## ATR Levels (video 04)
- ATR = Wilder's ATR(14). Day mode levels are measured from the previous close.
- Trigger at ±23.6% of ATR (the call/long and put/short triggers).
- Mid-range target at ±61.8%: "the first possible profit target".
- Full range at ±1 ATR: "the possible ideal profit target". Stocks rarely move more than 1 ATR
  in a day. Extensions are off by default.
- Range meter: today's range as a share of the ATR. Green under 70%, yellow from 70%, red from
  90%. Shows whether there is still "room to move".
- Day mode works best on 30-minute or lower charts. He uses the 10-minute for trend and the
  3-minute for entries.
- Not meant to be used alone: combine it with price, volume and trend (Ripster EMA clouds).

## Pivot Ribbon (video 05)
- 8 / 21 / 34 EMA. The 21 is the pivot; the 8 is where to add and cut; the 34 confirms a trend
  change (Ripster's 34/50 golden rule).
- Stacked and fanned out = strong trend. A ribbon fold or flip = trend change. A thin ribbon
  that folds often = chop, so don't trade.
- Day trade: 10-minute chart. Scalps: 3-minute. Swings: daily.
- Entries on pullbacks to the ribbon (8 / 21) near a level, after the trigger breaks and the
  ribbon has flipped.
- Stop: start just past the trigger, then trail it on the 34.
- Exits: partials at the mid-range, the rest near the full ATR.

## Day-trade checklist (video 06)
Mantra: play the trend, not a side. No trades in the first 10 minutes. No trades in a sideways
market. Wait for trend confirmation (price above the EMAs). Never chase: wait for a pullback.
Don't fight a trend that has momentum.

Bullish, essential (coded):
1. Trend: price above Ripster's 34/50 EMA cloud on the 10-minute. Above the 5/12 too is
   "extra bullish" (`sd_above_512`).
2. A 10-minute close above the call trigger.
3. Less than 70% of the ATR covered (`sd_max_range`).

Bullish, ideal:
4. "Super bullish": close > 5 > 8 > 12 > 21 > 34 > 50 EMA (`sd_stacked`, coded).
5. A TTM squeeze firing up (not coded).
6. RSI not overbought (not coded).

Entry: wait for the pullback to the 8/9 EMA on the 10-minute (or the 21) and enter as it
bounces. On the 3-minute, the 34/50 cloud is the pivot and the stop.
Stop: under the 34/50 cloud, or under the call trigger, or the previous close, depending on
risk (`sd_stop`).
Exits: first target is the mid-range (scale out there), add on pullbacks to the 21, and let
runners ride the 5/12 cloud to the full ATR.
Bearish: the mirror image (below the 34/50, a 10-minute close below the put trigger). Long-only
here, so a bearish read just means no longs.

## Full levels and modes (video 08)
- Levels: 23.6, 38.2, 50, 61.8, 78.6 and 100% of ATR, with extensions out to 3 ATR. Use them as
  support and resistance, and for scaling in and out. Scalp from one level to the next.
- Modes: day = daily ATR (charts of 1–30 minutes; he uses the 10-minute with **extended hours
  on**, and the 3-minute for scalps). Multi-day = weekly ATR. Swing = monthly. Position = quarterly.
  Long-term = yearly.

## Pivot Ribbon updates and Time Warp (videos CA, 09)
- The ribbon is now 8 / 13 / 21 / 34 / 48. The 8–13 zone is the precision entry and the place
  to stay in a trend. "If it closes below the 13 to 21 zone, exit." The 48 is where the trend
  is likely to change if broken.
- Conviction arrow = 13/48 crossover. Not a buy or sell signal; it usually pulls back after
  firing.
- Time Warp: the ribbon from a higher timeframe on a lower-timeframe chart. Day trades: 3-minute
  candles with the 10-minute ribbon. Scalps: 1-minute with the 3-minute ribbon. Enter on
  pullbacks into the 8–13 zone; risk under the 21, 34 or 48, the trigger, or the low of the day.
  Hold while no 10-minute candle closes below the 13 (or the 21). Exit when price structure
  shifts (a lower low after a higher high).
- The previous close is the day's bias: above it is bullish.
- Inside the trigger box, a break of one trigger often goes to the previous close, then to the
  other trigger.

## Golden Gate (video 13)
- Once price reaches 38.2% (the golden gate), there's about a 60% chance it reaches 61.8% (the
  golden fib) that session. Any ATR level reached leads to the next one more than 70% of the
  time. These are his tester's statistics.
- He adds a clear trend (stacked ribbon) for better entries. Enter on pullbacks that hold the
  13 / 21 / 34 / 48, on a reclaim of the trigger, or on a bull flag. Take profit at 50% and
  61.8%, and leave runners.
- It's fractal: the same odds hold in multi-day, swing and position modes.
- **Checked on our 17 tickers, Mar–Oct 2026:** 38.2% → 61.8% happened 51–57% of the time.
  Next level after the trigger, 38.2% and 50%: 72–76%. Beyond 61.8%: about 60%.
  `python3 -m bot.gate_stats`.

## Vomy / iVomy (video 11)
- Vomy (bearish): a stacked bullish ribbon meets resistance (often a level such as 38.2%),
  breaks the 8 and 13, tests the 48, then retests the 13. The entry is on the retest of the 13.
  It's confirmed by a break and hold of the 48. Take profits at ATR levels (previous close,
  trigger).
- iVomy is the bullish mirror image (a long setup).

## Bias candles (video 12)
- Candle colour relative to the 21 EMA. Green: up above the 21. Blue: down above the 21 (a
  pullback). Red: down below the 21. Orange: up below the 21. A visual aid for holding a trend;
  it adds no new rule.

## Trade plan (video 10)
- Setup / thesis, trigger, entry (on a break, retest or continuation; never chase), profit
  levels or stop moves, and a technical stop. Once price passes the previous close, move the
  stop there. A break above 38.2% targets the 50% mid-range.

## Still not coded
TTM squeeze, RSI / "Ready Aim Fire", trend lines, VIX pivots, options flow, and the 3-minute
and 1-minute charts. Our data is 5-minute, regular hours only. Saty uses extended hours for his
EMAs. The `SATY` variants (gate_touch, gate_retest, flag, ivomy, ...) were coded before the
transcripts arrived, from the indicator code alone.

## Backtest (17 tickers, 5-minute bars, long only, R per trade)
| | Mar–Aug (built on) | Sep–Oct (hold-out) | Hold-out, 2x slippage |
|---|---|---|---|
| SATYD essentials only | −0.05R (391 trades) | −0.06R (130) | |
| SATYD + super-bullish stack | 0.00R (225) | +0.09R (75) | +0.02R |
| SATYG golden gate (10m ribbon, 5m entries, 13-EMA exit only after T1, T2 at 1 ATR) | 0.00R (197) | +0.03R (93) | −0.05R |
| SATYG, 13-EMA exit from the start (as in the videos) | −0.06R (257) | not run | |
| SATYD + SATYG together | | +0.05R (120) | |
| SATYG with Time Warp: 3m entries, 10m ribbon incl. extended hours (`bot.timewarp`), Sep 14–Oct 27 only | | −0.10R (170) as in the videos; −0.07R (133) exit after T1 | |
| SATYG on 5m bars, the same Sep 14–Oct 27 days, for comparison | | +0.00R (97) / +0.06R (79) | |

All are about breakeven. The level statistics hold, but these mechanical entries and exits don't
turn them into an edge on stocks with 5-minute bars.

    python3 -m bot.backtest --symbols RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR --setups SATYD,SATYG --set sd_stacked=true --set sg_exit_after_t1=true --set sg_t2=1.0
