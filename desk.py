#!/usr/bin/env python3
"""Trading desk switch and runner.

  ./desk.py status                     show on/off, mode, phase and today's counts
  ./desk.py on [--until HH:MM]         let the desk trade (inside the entry windows)
  ./desk.py off                        stop new cycles
  ./desk.py window 09:35-11:30 [...]   set the entry windows
  ./desk.py mode paper|live            paper (default) records orders, live sends them
  ./desk.py kill / unkill              block every order, including exits
  ./desk.py run                        run a cycle every `cycle_minutes` (3) while the desk is on
  ./desk.py once [--phase P] [--dry-run]   run one cycle now
  ./desk.py journal [YYYY-MM-DD]       print the order journal for a day
  ./desk.py mcp-name NAME              point the agents at your Robinhood MCP server name
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import desklib

AGENTS_DIR = desklib.ROOT / ".claude" / "agents"


def cmd_status(args):
    cfg, state = desklib.load_config(), desklib.load_state()
    now = desklib.utcnow()
    day = desklib.local_now(cfg, now).date()
    mode = state["mode"]
    print(f"desk:        {'ON' if desklib.is_enabled(state, now) else 'off'}"
          + (f" until {_fmt_until(cfg, state)}" if state.get('enabled_until') else ""))
    print(f"mode:        {mode.upper()}")
    print(f"kill switch: {'ON - all orders blocked' if state['killed'] else 'off'}")
    print(f"phase now:   {desklib.phase(cfg, state, now)}  ({desklib.local_now(cfg, now):%a %H:%M %Z})")
    print(f"windows:     {', '.join(w['start'] + '-' + w['end'] for w in cfg['entry_windows'])}"
          f"  flatten {cfg['flatten_at']}")
    print(f"account:     {_mask(cfg.get('account_number')) or 'NOT SET'}")
    print(f"entries today: {desklib.entries_today(day, mode)}/{cfg['risk']['max_entries_per_day']}"
          f"   day trades (5d): {desklib.day_trades_last_5(day, mode)}/{cfg['risk']['max_day_trades_5d']}")


def cmd_on(args):
    cfg, state = desklib.load_config(), desklib.load_state()
    if not cfg.get("account_number"):
        sys.exit("Set account_number in desk/config.json first.")
    state["enabled"] = True
    state["enabled_until"] = None
    if args.until:
        local = desklib.local_now(cfg)
        until = dt.datetime.combine(local.date(), desklib.parse_hm(args.until), tzinfo=desklib.tz(cfg))
        if until <= local:
            sys.exit(f"{args.until} has already passed today.")
        state["enabled_until"] = until.astimezone(dt.timezone.utc).isoformat()
    desklib.save_state(state)
    cmd_status(args)


def cmd_off(args):
    state = desklib.load_state()
    state["enabled"] = False
    state["enabled_until"] = None
    desklib.save_state(state)
    print("Desk off. No new cycles. Open positions keep their stop orders at Robinhood.")


def cmd_window(args):
    cfg = desklib.load_config()
    windows = []
    for spec in args.windows:
        m = re.fullmatch(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", spec)
        if not m or desklib.parse_hm(m[1]) >= desklib.parse_hm(m[2]):
            sys.exit(f"Bad window {spec!r}; use HH:MM-HH:MM in ET, e.g. 09:35-11:30")
        windows.append({"start": m[1].zfill(5), "end": m[2].zfill(5)})
    cfg["entry_windows"] = windows
    desklib.save_config(cfg)
    print("Entry windows:", ", ".join(w["start"] + "-" + w["end"] for w in windows))


def cmd_mode(args):
    state = desklib.load_state()
    if args.mode == "live" and state["mode"] != "live":
        answer = input("LIVE mode sends real orders with real money. Type LIVE to confirm: ")
        if answer.strip() != "LIVE":
            sys.exit("Not changed.")
    state["mode"] = args.mode
    desklib.save_state(state)
    print(f"Mode: {args.mode.upper()}")


def cmd_kill(args):
    state = desklib.load_state()
    state["killed"] = args.command == "kill"
    if state["killed"]:
        state["enabled"] = False
    desklib.save_state(state)
    print("Kill switch ON: every order is blocked, including exits. Manage positions in the Robinhood app."
          if state["killed"] else "Kill switch off. The desk is still off until `desk on`.")


def cmd_journal(args):
    cfg = desklib.load_config()
    day = dt.date.fromisoformat(args.day) if args.day else desklib.local_now(cfg).date()
    for r in desklib.read_journal(day):
        price = r.get("limit_price") or r.get("stop_price") or ""
        print(f"{r['ts'][11:16]}Z {r['kind']:<11} {r['side']:<4} {r.get('quantity') or '':>5} "
              f"{r['symbol']:<6} {r['type']:<11} {price:<9} {r.get('reason', '')}")


def cmd_mcp_name(args):
    cfg = desklib.load_config()
    old = cfg.get("mcp_server", "robinhood-trading")
    for path in AGENTS_DIR.glob("*.md"):
        path.write_text(path.read_text().replace(f"mcp__{old}__", f"mcp__{args.name}__"))
    cfg["mcp_server"] = args.name
    desklib.save_config(cfg)
    print(f"Agents now use mcp__{args.name}__* tools.")


# ---- runner ----------------------------------------------------------------

def build_prompt(cfg, state, phase, now=None):
    context = {"phase": phase, "mode": state["mode"], "account_number": cfg["account_number"],
               **desklib.session_times(cfg, now)}
    return "/desk-cycle " + json.dumps(context)


def claude_command(cfg, prompt):
    server = cfg.get("mcp_server", "robinhood-trading")
    allowed = ["Agent", "Task", "Read", "Glob", "Grep", "Write", "WebSearch", "WebFetch", f"mcp__{server}"]
    return ["claude", "-p", prompt, "--allowedTools", *allowed, "--disallowedTools", "Bash",
            "--output-format", "text"]


def run_cycle(cfg, state, phase, dry_run=False):
    now = desklib.utcnow()
    local = desklib.local_now(cfg, now)
    prompt = build_prompt(cfg, state, phase, now)
    cmd = claude_command(cfg, prompt)
    if dry_run:
        print(" ".join(cmd[:2]), json.dumps(cmd[2]), " ".join(cmd[3:]))
        return
    log = desklib.DESK / "runs" / local.date().isoformat() / f"{local:%H%M}-{phase}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{local:%H:%M:%S}] cycle: {phase} ({state['mode']})", flush=True)
    timeout = cfg.get("cycle_timeout_seconds") or cfg["cycle_minutes"] * 60 - 15
    env = {**os.environ, "DESK_AGENT": "1"}
    try:
        result = subprocess.run(cmd, cwd=desklib.ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
    except subprocess.TimeoutExpired:
        output = f"Cycle timed out after {timeout}s."
    log.write_text(output)
    print(output.strip()[-1500:], flush=True)


def next_tick(cfg, now):
    """Next bar close (every `cycle_minutes`, aligned to the clock, so 9:30, 9:33, ... for 3) plus
    20 seconds, so each cycle sees a finished bar. A cycle that runs long skips the next tick."""
    step = cfg["cycle_minutes"] * 60
    epoch = int(now.timestamp())
    return dt.datetime.fromtimestamp((epoch // step + 1) * step + 20, dt.timezone.utc)


def expire(state, now):
    if state.get("enabled") and state.get("enabled_until") and not desklib.is_enabled(state, now):
        state["enabled"] = False
        state["enabled_until"] = None
        desklib.save_state(state)
        print("Desk switched itself off: the --until time passed.", flush=True)
    return state


def cmd_run(args):
    print("Desk runner started. Ctrl+C to stop the runner (`desk off` stops trading but keeps it running).",
          flush=True)
    while True:
        cfg = desklib.load_config()
        now = desklib.utcnow()
        state = expire(desklib.load_state(), now)
        phase = desklib.phase(cfg, state, now)
        if phase in ("entries", "manage", "flatten"):
            run_cycle(cfg, state, phase)
        wake = next_tick(cfg, desklib.utcnow())
        time.sleep(max(1, (wake - desklib.utcnow()).total_seconds()))


def cmd_once(args):
    cfg = desklib.load_config()
    state = desklib.load_state()
    phase = args.phase or desklib.phase(cfg, state)
    if phase not in ("entries", "manage", "flatten"):
        sys.exit(f"Phase is {phase!r}; nothing to do. Use --phase manage|flatten|entries to force one.")
    run_cycle(cfg, state, phase, dry_run=args.dry_run)


def _mask(number):
    return f"••••{number[-4:]}" if number else ""


def _fmt_until(cfg, state):
    until = dt.datetime.fromisoformat(state["enabled_until"]).astimezone(desklib.tz(cfg))
    return until.strftime("%H:%M %Z")


def main(argv=None):
    p = argparse.ArgumentParser(prog="desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    on = sub.add_parser("on")
    on.add_argument("--until", help="switch off automatically at HH:MM ET today")
    on.set_defaults(fn=cmd_on)
    sub.add_parser("off").set_defaults(fn=cmd_off)
    win = sub.add_parser("window")
    win.add_argument("windows", nargs="+", metavar="HH:MM-HH:MM")
    win.set_defaults(fn=cmd_window)
    mode = sub.add_parser("mode")
    mode.add_argument("mode", choices=["paper", "live"])
    mode.set_defaults(fn=cmd_mode)
    sub.add_parser("kill").set_defaults(fn=cmd_kill)
    sub.add_parser("unkill").set_defaults(fn=cmd_kill)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    once = sub.add_parser("once")
    once.add_argument("--phase", choices=["entries", "manage", "flatten"])
    once.add_argument("--dry-run", action="store_true", help="print the claude command instead of running it")
    once.set_defaults(fn=cmd_once)
    jr = sub.add_parser("journal")
    jr.add_argument("day", nargs="?")
    jr.set_defaults(fn=cmd_journal)
    mcp = sub.add_parser("mcp-name")
    mcp.add_argument("name")
    mcp.set_defaults(fn=cmd_mcp_name)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
