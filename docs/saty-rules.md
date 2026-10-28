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
- **B1–B3** Basic Trading, episodes 1–3: Managing Yourself, A Trading Flywheel, The Trading System

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

## Basic Trading series (episodes 1–3)
His general rules, and where the desk already applies each one:

| Rule (episode) | On the desk |
|---|---|
| Fixed risk per trade (R) and a cap per day or week (B1) | `risk_per_trade_usd`, `max_daily_loss_usd` in `desk/config.json` |
| Size from a technical stop: shares = R ÷ (entry − stop), not a fixed % stop (B1, B3) | the risk-manager agent sizes exactly this way |
| Wins bigger than losses, losses small; aim for at least 2R (B1) | `min_reward_risk` = 2.0 |
| Never size up after a winning streak; position size stays consistent (B1, B3) | sizing is fixed by config and the agents can't change it |
| Entry conditions, profit levels and failure point are known before entering (B3) | each plan in `desk/notes/plans/` holds entry, stop and target |
| When the system says exit, exit: "trade like an algo" (B1, B3) | the trader agent follows the plan; the guard hook always allows exits |
| Trade liquid names with tight spreads (B3) | strategy.md skips spreads wider than 0.2% |
| Journal every trade: reason in and out, and what happened afterwards (B1) | the guard journal records orders; reasons are in the run reports |
| Don't keep changing the system; change it only against a tested baseline (B1) | applies to how this backtesting is done: rules are fixed before the hold-out |
| Keep day trades, short swings and longer positions in separate accounts (B2) | the desk trades one Agentic account, day trades only by default |

Episode 2 lists the setups for later episodes: Vomy, Phase Oscillator divergences, continuation
trades, flags, and the squeeze for swings. Those episodes aren't out yet.

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
| SATYG gate touch (`sg_entry=touch`): buy-stop at 38.2%, stacked 10m ribbon confirms, stop under the call trigger, T1 50% / T2 61.8%, ribbon hold after T1 | −0.08R (159), win 55% | −0.27R (39), win 46% | |
| Same, T1 61.8% / T2 full ATR | −0.16R (159), win 39% | −0.28R (39), win 33% | |
| Gate touch, no ribbon, all out at 61.8%, zero costs | 0.00R (358), win 40% | +0.02R (98), win 39% | |

### The desk playbook (`strategy.md`), as the agents trade it: `python3 -m bot.saty_desk`
17 tickers, Mar 9 – Oct 27 2026, setups A + B + C, scale-outs in thirds at ATR levels.

| Version | Trades | Win | Avg R |
|---|---|---|---|
| As written, 0.03% slippage per fill | 1420 | 42% | −0.09R |
| Mar 9 – Sep 11 (5-minute entry candles) | 1128 | 41% | −0.09R |
| Sep 14 – Oct 27 (3-minute candles, 1-minute fills) | 293 | 45% | −0.10R |
| With the desk's caps (3 entries a day, 2 open, 2.5R daily stop) | 440 | 46% | −0.09R |
| 13-EMA exit only after the first scale-out | 1384 | 46% | −0.09R |
| Zero costs | 1416 | 53% | +0.03R |
| Double slippage | 1409 | 31% | −0.20R |

Trend lines and classic technical analysis (all on by default): a trend-line break entry (a close
through a falling line drawn across the day's last two lower swing highs), the previous day's high
and the premarket high as extra break/retest levels, and Saty's structure exit (a lower swing low
after a higher swing high). Mar–Aug: −0.09R → −0.08R a trade (1389 trades); trend-line entries
alone: 14 trades, −0.32R.

### Options in a $1,000 account: `python3 -m bot.saty_options`
Every playbook trade replayed with calls: first strike above the price (at most two target levels
up), first Friday at least 4 days out, at most $150 premium and $30 loss at the technical stop per
trade, half-spread 2.5% of premium (≥ $0.02) per fill, PDT limit of 3 day trades in 5 days.
Black-Scholes with implied vol = 1.3 × 20-day realized vol (RKLB's real Oct 30 calls on Oct 27
fit about 1.4×). Robinhood keeps no intraday history for expired contracts, so the prices are
modeled.

| Version | Option trades | Win | Result on $1,000 |
|---|---|---|---|
| 4 days to expiry, $150 cap | 32 | 19% | −$150 |
| 2 days | 58 | 16% | −$384 |
| 1 day | 67 | 21% | −$310 |
| $250 cap, $40 risk | 71 | 23% | −$465 |
| Cash account (no PDT, next-day settlement) | 40 | 20% | −$176 |
| Every signal, no cost cap | 1829 | 19% | −4.5% a trade |

1807 of 1847 signals were too expensive for a $150 cap: a 4-day near-the-money call costs a median
$170 (HIMS) to $10,500 (SNDK) per contract. Winning stock trades only make +0.7% in the option
(the targets are close and the spread costs ~5% a round trip); losing ones lose 9.7%.

### Tuning the share trades (17 tickers), walk-forward
Each idea judged on Mar–Jul (both Mar–May and Jun–Jul, normal and 2× slippage), the survivors
combined, checked on August, then run once on Sep–Oct. Average R a trade:

| Idea (Mar–Jul) | Mar–May | Jun–Jul | 2× slip | |
|---|---|---|---|---|
| Baseline | −0.062 | −0.114 | −0.192 | |
| Stop under the 34 / the 48 (`--stop-mode e34/e48`) | −0.058 / −0.056 | −0.100 / −0.093 | −0.166 / −0.150 | 48 passes |
| Stop under the call trigger | −0.098 | −0.098 | −0.195 | fails |
| Limit entries on the pullback (`--entry-order limit`) | −0.093 | −0.139 | −0.207 | fails |
| Golden-gate context only (`--a-only`) | −0.033 | −0.110 | −0.165 | passes |
| Super-bullish stack (`--super-stack`) | −0.076 | −0.095 | −0.180 | fails |
| 10m squeeze momentum | −0.063 | −0.129 | −0.195 | fails |
| SPY ribbon bullish / + VIX (`--market spy/spy_vix`) | −0.100 / −0.178 | −0.123 / −0.113 | −0.213 / −0.254 | fail |
| Mornings only (`--window morning`) | −0.036 | −0.097 | −0.160 | passes |
| First target ≥ 1R (`--min-target-r 1`) | −0.020 | −0.083 | −0.159 | passes |
| **A + mornings + target ≥ 1R** (chosen) | +0.060 | +0.006 | −0.065 | |
| All four passing ideas (runner-up) | +0.023 | −0.036 | −0.073 | |

| Out of sample | August | Sep–Oct | Sep–Oct, 2× slip |
|---|---|---|---|
| Baseline | −0.089 (229) | −0.180 (463) | −0.283 |
| Chosen: A + mornings + target ≥ 1R | −0.004 (121) | −0.050 (286) | −0.161 |
| Runner-up: all four | +0.137 (81) | +0.019 (201) | −0.066 |

Swing on weekly ATR levels and the daily ribbon (`python3 -m bot.saty_swing`): 51 trades,
0.00R a trade on Sep 2024 – Jul 2026; August −0.41R (4), Sep–Oct −0.23R (5).

### SPY same-day (0DTE) options, as in Saty's daily plans: `python3 -m bot.saty_0dte`
The playbook on SPY in both directions (the bearish side runs the same rules on SPY reflected
around a price: put trigger, short golden gate, Vomy, bear flags). Strike ~1 ATR level out of the
money (his posted strikes, e.g. SPY 770c / 764p with SPY ~767), VIX key level as a filter, no
trading on monthly OPEX, out by 15:40 (Robinhood closes 0DTE at 15:45), $100 premium a trade,
$1,000 account. Prices: Black-Scholes with IV = VIX × 0.8 (calls) / × 1.0 (puts), fitted to SPY's
real 770c and 764p on 2026-10-27 (`data/options/SPY_0dte_2026-10-27.json`); half-spread
max($0.01, 1%).

| Version, Mar 9 – Oct 27 | Trades | Win | $1,000 → |
|---|---|---|---|
| Margin account (3 day trades / 5 days) | 69 | 33% | $1,356 (max drawdown −$396) |
| Cash account (no PDT, next-day settlement) | 101 | 34% | $1,693 |
| $50 a trade | 63 | 32% | $1,456 |
| No VIX filter | 88 | 30% | $1,603 |
| At-the-money strikes | 70 | 29% | $1,401 |
| Pricier options (IV × 1.0 calls / × 1.2 puts) | 67 | 31% | $1,206 |
| Cheaper options (× 0.7 / × 0.9) | 69 | 32% | $1,175 |
| 3% half-spread | 69 | 33% | $1,209 |
| Mar–Aug only / Sep–Oct only | 53 / 16 | 34% / 31% | +$202 / +$121 |
| Calls only / puts only | 56 / 27 | 36% / 26% | +$292 / +$251 |

Positive in every variant, but: the profit comes from a few big winners (without the best 3 trades
the margin account is −$104; the median trade is −$7), and it isn't statistically clear of luck
(t = 0.8–1.1; bootstrap: 13–23% chance the true total is ≤ 0). The price model is fitted to one
day: on 2026-10-27 it was close in the morning but overpriced calls by 30–40% after midday, and
my VIX key level (17.5) differed from Saty's posted one (18). Paper trade it before any money.

TTM squeeze filters (`--squeeze`, 10-minute bars; `--daily-squeeze`, daily bars), all entry types:

| Filter | Mar–Aug | Sep–Oct hold-out |
|---|---|---|
| none | −0.09R (1399) | −0.15R (461) |
| 10m squeeze momentum up | −0.08R (1010) | −0.12R (326) |
| 10m fired up within the hour | −0.11R (376) | not run |
| daily squeeze momentum up | −0.05R (388) | −0.29R (144) |

With all of Saty's entry types (pullback, break and hold, retest, opening-range breakout, bull
flag; `--entries`), the playbook's current default:

| Version | Trades | Win | Avg R |
|---|---|---|---|
| All entry types | 1860 | 41% | −0.11R |
| Sep 14 – Oct 27 (3-minute candles) | 386 | 39% | −0.16R |
| With the desk's caps | 462 | 47% | −0.05R |
| Zero costs | 1854 | 50% | +0.03R |
| By entry type: pullback / retest / break / ORB / flag / iVomy | 846 / 348 / 444 / 53 / 82 / 87 | 40 / 41 / 45 / 32 / 29 / 40% | −0.08 / −0.07 / −0.15 / −0.14 / −0.20 / −0.17R |

Median stop is 0.64% of price, so slippage alone is about 0.09R a trade. Before costs the
playbook is slightly positive; after them it loses.

The gate trade's stop (38.2% down to the 23.6% trigger) is only 14.6% of the daily ATR, so
0.03% slippage per fill costs about 0.11R a trade. Ribbon confirmation didn't help it.

All are about breakeven. The level statistics hold, but these mechanical entries and exits don't
turn them into an edge on stocks with 5-minute bars.

    python3 -m bot.backtest --symbols RKLB,BE,TSLA,MSFT,GOOGL,AAPL,META,SPY,QQQ,MRVL,HOOD,AMD,AMZN,HIMS,MU,SNDK,PLTR --setups SATYD,SATYG --set sd_stacked=true --set sg_exit_after_t1=true --set sg_t2=1.0

## GG reload on SPY 0DTE (Saty's SPX trade, 2026-10-27) - `python3 -m bot.saty_ggreload`

Rules, fixed before testing (from his chart notes): gate unlocks (+38.2%) -> Phase Oscillator
cools to 23.6 or a 3m close under the 21 EMA ("PO needs to cool") -> no 3m close under PDC since the
unlock -> first 3m close back above the 21 EMA, between PDC and the gate, 9:40-13:00 -> buy-stop
above that candle; out on a 3m close under PDC (hard stop 0.05 ATR under it) -> same-day golden-fib
strike, scale out at trigger / gate / mid, rest at the golden fib; out by 15:40. Puts = mirror.
Phase Oscillator coded from his Pine source (21 EMA pivot, 3 x ATR(14), 3 EMA, Bollinger compression).

Oct 27 check: entry 9:57 (his note 9:59), out 11:41 at the golden fib. SPY 770c, model vs real 5m
close: entry 0.15 vs 0.22; exits 0.20/0.38/0.61/0.89 vs 0.21/0.32/0.51/0.74. Model +229%, real about
+100%: the model is generous on this trade.

$1,000 margin account, $100 a trade, Mar 9 - Oct 27, 2026:

| period | setups | option trades | win | P&L |
|---|---|---|---|---|
| Mar-Jul (build) | 20 | 17 | 53% | -$55 |
| Aug (check) | 7 | 5 | 60% | -$24 |
| Sep-Oct 27 (hold-out) | 7 | 7 | 57% | +$386 |
| all | 34 | 29 | 55% | +$308, max drawdown -$283 |

Without the best trade +$91; without the best 3 -$220. Variants (all / Mar-Jul): enter on trigger
reclaim +$258 / +$102; cool at 61.8 same as 23.6; VIX key-level filter -$378 / -$196; ATM strike
+$275 / +$44; cash account +$229; call IV x1.0 +$133 / +$6; 3% spread +$277 / -$71.
Verdict: about one setup a week; the edge rests on a few golden-fib days (Jun 24, Oct 27), and the
build period alone is flat-to-negative. Not proven; paper trade it before money.

## 5-minute ORB on stocks in play (Zarattini, Barbon & Aziz 2024, SSRN 4729284) - `python3 -m bot.orb_paper`

Their rules, unchanged: first 5m candle sets the side (green: buy-stop at its high; red: sell-stop
at its low; doji: none), stop 10% of ATR(14), out at 16:00; price > $5, 14-day volume >= 1M,
ATR > $0.50, opening-range relative volume >= 100%, top 20 by it; 1% risk, 4x leverage, $0.0035/share.
Paper (7,000 stocks, 2016-2023): +1,637%, Sharpe 2.81, +0.08 to +0.38R a trade by relative volume.

Our 17 tickers, Mar 9 - Oct 27, 2026 ($25,000):

| run | trades | win | R/trade net | account | Sharpe | max DD |
|---|---|---|---|---|---|---|
| paper rules | 783 | 21% | +0.42 | +197% | 3.38 | -8.6% |
| + $0.01/share slippage each fill | 783 | 21% | +0.40 | +179% | 3.21 | -8.9% |
| + $0.02/share | 783 | 20% | +0.38 | +163% | 3.03 | -9.3% |
| no relative-volume filter (their "base") | 2,301 | 20% | +0.22 | +96% | 3.54 | -6.3% |
| without SNDK and PLTR | 691 | 18% | +0.18 | +60% | 1.83 | -11.5% |
| $1,000 cash account, long only, top stock, no leverage | 120 | 22% | +0.70 | +53% | 2.18 | -4.5% |

Every month positive with the paper's rules (+20 to +94R). Longs +0.69R, shorts +0.13R.
Check of the bar data: Sep 14 - Oct 27 on real 1m bars +0.357R vs the same days on 5m bars +0.364R.
Stop sensitivity (not tuned, the paper's 10% kept): 5% ATR +1.05R, 20% ATR +0.16R.
Caveats: 8 months of a bull market; our tickers were chosen after they ran (SNDK, PLTR give half
the R); 10% ATR stops are tight, so real fills matter more than in a backtest; a $1,000 margin
account can't short and hits the 3-day-trade limit (cash account: one round trip a day settles T+1).

## ORB + Saty ATR levels + Pivot Ribbon - `python3 -m bot.orb_saty --grid [--or-minutes 5]`

288 combinations: entry (range break / must also clear the trigger / first ATR level beyond the
range) x 10m ribbon confirmation (none / 8>21 / over the 34-48 cloud / fully stacked) x stop (10%
ATR / range middle / ATR level below) x exit (16:00 / 10m close under 13 / under 48 / thirds at
the next two levels) x relative-volume filter. 17 tickers, $0.01/share slippage each fill.
Build Mar 9 - Jul 31, check Aug, hold-out Sep 1 - Oct 27; R per trade:

| version | build | check | hold-out |
|---|---|---|---|
| 15m ORB, paper rules + relvol | +0.23 (461) | +0.60 (75) | -0.11 (186) |
| 15m best on build: trigger + cloud + level stop | +0.45 (345) | +0.72 (57) | -0.33 (128) |
| 5m ORB, paper rules + relvol | +0.49 (473) | +0.45 (81) | +0.29 (209) |
| 5m best on build: trigger + fully stacked ribbon | +0.65 (228) | +0.39 (40) | -0.10 (98) |
| 5m + ribbon 8>21 | +0.46 (297) | +0.76 (57) | +0.40 (123) |
| 5m + ribbon 8>21, entries by 10:30 | +0.53 (285) | +0.85 (51) | +0.40 (115) |
| 5m + ribbon 8>21, $0.03 slippage | +0.42 | +0.71 | +0.35 |

Verdict: the 15-minute range loses in the hold-out with every strong filter; the strict build
winners overfit in both. The plain 5-minute paper rules hold in all three periods; the 8>21 ribbon
filter improved check and hold-out but was picked after seeing them, so it is a lead, not proof.
Across all combinations: relative volume helps most; ATR-level entries beyond the range, the
range-middle stop and the 13-EMA exit hurt; holding to the close beats every ribbon exit.

## ORB signals as options, contracts by the Enrich Trades pricing guide - `python3 -m bot.orb_options`

Signal: 5m ORB + relative volume + 10m ribbon 8>21 (calls on green ranges, puts on red), out on
the stock stop or at the close. Guide checklist: spread < 10% of mid, volume and OI > 500, IV
normal for the stock, earnings accounted for, delta 0.30-0.50, enough time for theta.
Real chain, 2026-10-27 close, ATM, Nov 6 (data/options/chain_snapshot_2026-10-27.json): cheapest
contracts HIMS $135 and RKLB $299; SPY $787; most others $900-$6,400. Spread fails: HIMS 11.2%,
MU 10.3%. Volume and OI pass: TSLA, AAPL, SPY, AMZN, PLTR. IV / 20-day realized vol: 0.9-1.3
without earnings before expiry, 1.6-2.5 with (MSFT, GOOGL, AAPL, AMZN, PLTR, HOOD, SNDK).
Model: IV = realized x 1.2 (SPY, QQQ x 1.4), x 1.8 when earnings fall before expiry; half the real
spread per fill. Real check on six live contracts: real avg +1%, model +5% a trade.

Every signal, one contract, no cost cap (option return on premium; stock signals +0.51R):

| contract | trades | avg return | Mar-Jul | Aug | Sep-Oct |
|---|---|---|---|---|---|
| delta 0.2, 7 days | 386 | +1.3% | +0.8% | +4.2% | +1.1% |
| delta 0.4, 7 days (guide) | 386 | +0.5% | +0.2% | +2.5% | +0.3% |
| delta 0.7, 7 days | 386 | -0.6% | -0.9% | +0.7% | -0.6% |
| delta 0.4, 2 days | 401 | +2.6% | +1.1% | +9.2% | +3.3% |
| delta 0.4, 14 / 28 days | 354 / 297 | -1.2% / -1.8% | | | |
| trading through earnings | 428 | 0.0% | | | |
| full checklist tickers only | 130 | +3.4% | +2.7% | +13.4% | -0.4% |
| spreads twice as wide (first hour) | 386 | -4.2% | | | |

$1,000 cash account, guide defaults, $300 a trade: 25 trades, $1,000 -> $1,550 - but 359 of 426
signals were skipped because one contract cost more than $300, and PLTR's 4 trades made $624.
Verdict: the ORB edge per trade (~0.05 ATR) is about what an option's spread and a day of decay
cost, so the options version is roughly break-even; the guide's filters (tight spreads, no
earnings, liquid names) are what keep it from losing. Trade this signal in shares.

## 15m ORB + 5-minute ribbon hold + ATR-level take profits - `python3 -m bot.orb_saty --grid5`

Idea: bullish while 5m candles hold the 5m 13 EMA (8/13/21/34/48 on 5m bars, extended hours);
enter on the 15m opening-range break after the last three 5m closes held the 13; hold while 5m
closes stay over the 13; take profits at the ATR levels. Grid of 1,152: side from the range
candle or either break x range break / must clear the trigger x confirmation (none / held the 13 /
held the 13 with 8>21) x take a later re-break if the first wasn't confirmed x stop (10% ATR /
range middle / ATR level below / 5m 21 EMA) x exit (16:00 / 5m close under 13 / under 21 / thirds
at the next two ATR levels then the 13 / half at the next level then the 13 / thirds then 16:00)
x relative volume. $0.01/share slippage each fill.

Positive: build 1,150 of 1,152, check 906, hold-out (Sep 1 - Oct 27) only 344. The 25 best on the
build all lose in the hold-out (-0.16 to -0.43R a trade). Average R/trade on the build by choice:
confirmation none +0.10, held the 13 +0.09; exit 16:00 +0.13, thirds at ATR levels then 16:00
+0.125, 5m close under 21 +0.11, under 13 +0.08, ATR levels then the 13 +0.07; stop ATR level
+0.15, 10% ATR +0.10, 5m 21 EMA +0.07; relative volume +0.13 vs +0.07.

Same rules on the 5m opening range (relative volume, 10% ATR stop):

| version | build | check | hold-out |
|---|---|---|---|
| plain, to 16:00 | +0.49 (473) | +0.45 | +0.29 |
| 10m ribbon 8>21, to 16:00 | +0.46 (297) | +0.76 | +0.40 |
| held the 5m 13, to 16:00 | +0.25 (203) | +0.99 | +0.14 |
| held the 5m 13, out on a 5m close under it | +0.25 | +0.88 | +0.07 |
| held the 5m 13, ATR take profits, rest on the 13 | +0.26 | +0.40 | -0.04 |
| plain, ATR take profits, rest to 16:00 | +0.51 | +0.37 | +0.17 |

Verdict: the 5m 13 EMA is too close to price. As a filter it drops good breakouts (opening-range
candles often close under it before the trend starts); as an exit it shakes out of trend days on
normal pullbacks. ATR-level take profits trim the few big days that pay for the many small stops.
Keep: 5m range, relative volume, 10m 8>21 ribbon, 10% ATR stop, hold to the close.

## 15m ORB with ATR levels on MNQ, $1,000 - `python3 -m bot.orb_mnq`

No futures history here, so QQQ stands in: MNQ points = QQQ x 41.1 (NDX / QQQ at the month ends
Apr-Sep 2026: 41.08-41.11), $2 a point, so $1 of QQQ = $82 per contract; QQQ's ~$10 ATR = ~410
points = ~$820 per contract. Costs $2.70 per contract round trip (fees + a tick each side).
One contract at a 10% ATR stop risks ~$80, at the ATR level below ~$120: 8-12% of $1,000, and
scaling out needs 2+ contracts, so the exits are all-out at the next ATR level (tp1), the second
(tp2), 16:00, or a 10m close under the 13 EMA.

Grid of 288 (side from the range candle / either break x entry range / trigger / next ATR level
x ribbon none / 8>21 x stop 10% ATR / range middle / ATR level below x those exits x relative
volume): 31 build trades for the best settings (one index, ~1 a week); positive points/trade in
92 of 180 on the build, 90 in August, 111 in the hold-out - a coin flip. Averages on the build:
take profit at the next / second ATR level +6.2 / +6.7 points, hold to 16:00 -10.7, ATR-level
entry -13.8, relative volume +9.9 vs -3.3.

Best on the build (range break, range-middle stop, all out at the second ATR level, relative
volume): +44 / +63 / +16 points a trade (31 / 5 / 14 trades). In the $1,000 account:
- margin $1,000 a contract: one loss puts equity under the margin; trading stops at $895
- margin $100, 10% risk cap: after 2-3 losses one contract risks over 10%, every later trade is
  skipped; $977
- 25% risk allowed: $3,583 by July, about $9,000 at the peak, $2,266 at the end after a $6,833
  drawdown; the hold-out alone took $1,000 to $442
Verdict: no robust edge on one index with this few trades, and $1,000 is too small for MNQ at
these stops - one contract is 8-15% of the account a trade. Needs ~$5,000+ to keep a contract
at 2% risk.

## Same test on MES - `python3 -m bot.orb_mnq --contract MES`

SPY stands in: MES points = SPY x 10.03 (SPX / SPY at the month ends Apr-Sep 2026: 10.02-10.04),
$5 a point, so $1 of SPY = $50 per contract; SPY's ~$7.5 ATR = ~$375 per contract (MNQ ~$820).
One contract at a 10% ATR stop risks ~$40 (4% of $1,000), at the range middle ~$75-100. Costs
$4.20 per contract round trip (fees + a tick each side = 0.84 points).

Grid of 288: positive points/trade in only 9 of 180 on the build (Mar-Jul), 136 in August, 143 in
the hold-out; every choice averages negative on the build (-1.8 to -4.4 points a trade). Best on
the build (either break, must clear the trigger, range-middle stop, all out at the next ATR level,
relative volume): +2.4 / -2.7 / +1.3 points a trade (32 / 7 / 18 trades).
$1,000 account, margin $100 a contract, 10% risk cap, all eight months:

| version | end | max drawdown |
|---|---|---|
| best on build (trigger, range-middle stop, next level) | $1,267 | -$853 |
| range break, range-middle stop, next level | $1,193 | -$856 |
| range break, 10% ATR stop, next level | $622 | -$585 |
| range break, 10% ATR stop, to 16:00 | $335 | -$917 |
| range break, range-middle stop, second level | $768 | -$630 |

Verdict: MES fits $1,000 better than MNQ (one contract ~4-10% risk), but the 15m ORB has no edge
on the S&P - the same as SPY in the stock tests (-11R on the paper's rules). Not worth trading.
