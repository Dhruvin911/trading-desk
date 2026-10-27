# Trading desk: Claude agents + Robinhood

A team of Claude Code subagents that trades your Robinhood **Agentic** account on 5-minute
VWAP / EMA setups around PDH/PDL and PMH/PML. You switch it on and off, and it only opens trades
inside the time windows you set.

```
 scanner ──► technical / news / fundamentals analysts ──► risk-manager ──► trader ──► Robinhood
 levels &        read-only, run in parallel                sizes, applies    only agent that
 candidates                                                daily limits      can place orders
                          every order passes the guard hook (hard limits) ─┘
```

> Real money. Start in **paper mode** (the default) for at least a week or two, read the logs, and keep
> the position cap small. Nothing here is financial advice, and LLM agents make mistakes.

## Setup (on your computer)

1. Install [Claude Code](https://code.claude.com) and Python 3.9+.
2. Connect Robinhood to Claude Code:
   ```bash
   claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
   claude            # then type /mcp, pick robinhood-trading, sign in
   ```
   If you named the server something else: `./desk.py mcp-name <name>`.
3. Put your **Agentic** account number in `desk/config.json` → `account_number`
   (ask Claude "list my Robinhood accounts"; it's the one that agents can trade).
4. Adjust `desk/config.json` (watchlist, risk limits) and `strategy.md` (your setups, in plain English).
5. Check it: `python3 -m unittest discover -s tests` and `./desk.py status`.

## Daily use

```bash
./desk.py on --until 11:30        # switch on; switches itself off at 11:30 ET
./desk.py window 09:35-11:30      # change entry windows (ET), any number of them
./desk.py run                     # leave running in a terminal: one cycle per 5-min bar while on
./desk.py status                  # on/off, mode, phase, entries and day trades used
./desk.py off                     # stop (open positions keep their stop orders at Robinhood)
./desk.py kill                    # emergency: block every order, including exits
./desk.py journal                 # every order the guard saw today
./desk.py once --phase manage     # run a single cycle by hand
```

Each cycle's report is saved in `desk/runs/<date>/<HHMM>-<phase>.md` (summary) and `.log` (full output).

**Phases** (ET, weekdays, 9:30-16:00):

| Phase | When | What happens |
|---|---|---|
| `entries` | desk on, inside an entry window | manage open positions, look for new setups |
| `manage` | desk on, between windows | manage open positions only |
| `flatten` | desk on, after `flatten_at` (15:50) | close intraday positions |
| `off` / `closed` | desk off, or market closed | runner sleeps |

Market holidays aren't detected: the runner will run cycles, but there's no data and orders get rejected.

## Paper → live

```bash
./desk.py mode live      # asks you to type LIVE
./desk.py mode paper
```
In paper mode, every order goes through all the checks, is written to the journal, and is then blocked.
The agents track simulated fills in `desk/notes/plans/`.

For a half-way step, turn on **trade approvals** in the Robinhood app: in live mode each order then
waits for your tap in the app before it executes.

## Safety: what is enforced where

**Hard limits** (`.claude/hooks/order_guard.py`, runs before every order; the AI can't override it):
- only the `trader` subagent, only on the configured account
- options, crypto and option exercise always blocked
- entries: desk on, inside an entry window, watchlist symbols, `limit`/`stop_limit`, whole shares,
  regular hours, position ≤ `max_position_usd`, ≤ `max_entries_per_day`, day trades (last 5 business
  days) < `max_day_trades_5d`
- exits (sells) allowed whenever the kill switch is off, so it can always get out
- if the guard itself errors, the order is blocked

During cycles the agents can't use Bash and can only write to `desk/notes`, `desk/runs` and `desk/cache`.
They can't touch the config, the on/off state, the journal or the hooks.

**Soft limits** (the risk-manager agent's instructions; usually followed, not guaranteed):
`max_daily_loss_usd`, `max_open_positions`, `min_reward_risk`, confidence ≥ 3, news/earnings blocks,
sizing from `risk_per_trade_usd`.

**Known gaps**
- The day-trade and entry counts are based on orders the guard let through, not on fills. An order that
  Robinhood rejected still counts, which errs on the safe side.
- A protective stop is placed only after the entry fills, so a position can be unprotected for a moment
  or, if the fill comes after the run ends, until the next cycle (≤ 5 minutes).
- Under FINRA's pattern-day-trader rule, a margin account under $25k is limited to 3 day trades in
  5 business days. Robinhood's own pre-trade check also flags this. Check Robinhood's current rules.
- Each cycle starts several agents. Running every 5 minutes all day costs real tokens. Shorter windows
  cost less.

## Files

| Path | What it is |
|---|---|
| `strategy.md` | your setups in plain English; every agent reads it |
| `desk/config.json` | watchlist, windows, risk limits, account |
| `desk.py`, `desklib.py` | on/off switch, runner, clock and journal |
| `.claude/agents/*.md` | the six agents and which Robinhood tools each may use |
| `.claude/commands/desk-cycle.md` | the orchestrator for one cycle |
| `.claude/hooks/order_guard.py` | hard limits on orders |
| `.claude/hooks/write_guard.py` | keeps agents out of config/state during cycles |
| `tests/` | `python3 -m unittest discover -s tests` |
