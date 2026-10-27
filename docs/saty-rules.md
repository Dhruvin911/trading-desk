# Saty Mahajan's rules, from his videos

Summarised from the transcripts of three videos (Oct 2026). The transcripts themselves aren't
in the repo. Each rule shows where it is coded: `SATYD` in `bot/strategy.py`.

Videos:
- **04** ATR Levels Overview and Tutorial
- **05** Pivot Ribbon Indicator Tutorial
- **06** Day Trading with Saty ATR Levels and Ripster EMA Clouds (setups, entries, exits)

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

## Not in these three videos
The golden gate (38.2% to 61.8%), Vomy / iVomy, bias candles, conviction arrows and Time Warp
come from his other videos, which weren't transcribed. In code they are the `SATY` variants,
reconstructed from the indicators. They are not checked against the videos.

## Backtest (17 tickers, 5-minute bars, long only, R per trade)
| | Mar–Aug (built on) | Sep–Oct (hold-out) | Hold-out, 2x slippage |
|---|---|---|---|
| SATYD essentials only | −0.05R (391 trades) | −0.06R (130) | |
| SATYD + super-bullish stack | 0.00R (225) | +0.09R (75) | +0.02R |

    python3 -m bot.backtest --symbols RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR --setups SATYD --set sd_stacked=true
