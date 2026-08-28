#!/usr/bin/env python3
"""nightshift board - live status board for all Claude Code sessions on this machine.

Sorted by "how much does this one need you", most urgent first:
  waiting  blocked on a permission prompt   -> answer it
  draft    text typed but never submitted   -> press Enter
  idle     finished, awaiting instruction   -> give it work
  busy     working                          -> leave it alone

Usage:  nightshift board            live, refresh every 2s
        nightshift board -n 5       refresh every 5s
        nightshift board --once     print once
        nightshift board --brief    one line for the tmux status bar

Session discovery lives in core.py, shared with the office view.
"""
import os, sys, time, signal

from .core import collect, ago, RANK

R = "\033[0m"
CLR = dict(waiting="\033[31m", draft="\033[35m", idle="\033[32m",
           busy="\033[33m", unknown="\033[90m")
GLYPH = dict(waiting="!", draft="◆", idle="○", busy="●", unknown="·")
BOLD, GRY = "\033[1m", "\033[90m"


def render(rows, width):
    out = ["%s%s%-24s %-8s %-9s %-26s %6s %6s%s" % (
        GRY, BOLD, "SESSION", "STATE", "TMUX", "CWD", "AGE", "QUIET", R)]
    for r in rows:
        c = CLR[r["state"]]
        cwd = r["cwd"]
        if len(cwd) > 26:
            cwd = "…" + cwd[-25:]
        out.append("%s%s %-22s %-8s%s %-9s %-26s %6s %6s" % (
            c, GLYPH[r["state"]], r["name"][:22], r["state"], R,
            r["where"][:9], cwd, r["age"], r["quiet_str"]))
        line = ("↳ unsent: " + r["draft"]) if r["draft"] else r["snip"]
        if line:
            room = max(20, width - 4)
            if len(line) > room:
                line = line[:room - 1] + "…"
            out.append("  %s%s%s" % (CLR["draft"] if r["draft"] else GRY, line, R))
    n = {k: sum(1 for r in rows if r["state"] == k) for k in RANK}
    need = n["waiting"] + n["draft"] + n["idle"]
    out.append("")
    out.append("%s%d session(s) · %d busy · %d need you (%dw %dd %di) · %s%s" % (
        GRY, len(rows), n["busy"], need, n["waiting"], n["draft"], n["idle"],
        time.strftime("%H:%M:%S"), R))
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    if "--brief" in args:
        rows = collect()
        b = sum(1 for r in rows if r["state"] == "busy")
        w = sum(1 for r in rows if r["state"] in ("waiting", "draft"))
        print("CC %d busy%s" % (b, (" !%d" % w) if w else ""))
        return
    iv = 2.0
    if "-n" in args:
        try: iv = float(args[args.index("-n") + 1])
        except Exception: pass
    if "--once" in args or "-1" in args:
        print(render(collect(), 110)); return
    signal.signal(signal.SIGINT, lambda *a: (sys.stdout.write("\033[?25h\n"), sys.exit(0)))
    sys.stdout.write("\033[?25l")
    try:
        while True:
            try: width = os.get_terminal_size().columns
            except OSError: width = 110
            sys.stdout.write("\033[H\033[2J" + BOLD + "  Claude Code fleet\n" + R
                             + render(collect(), width) + "\n")
            sys.stdout.flush()
            time.sleep(iv)
    finally:
        sys.stdout.write("\033[?25h")



if __name__ == "__main__":
    main()
