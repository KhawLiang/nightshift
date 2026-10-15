#!/usr/bin/env python3
"""nightshift talk - read what any Claude Code session on this Mac is saying,
and say something back.

The office shows *state*; this shows *content*. Left: every running session,
grouped by workspace. Right: that session's conversation, tailed as it is
written, with a box to type into and the pane's live screen above it.

Claude Code appends one JSON object per line to
~/.claude/projects/<slug>/<sessionId>.jsonl, so following a conversation is a
byte-offset tail - the browser sends back the offset it stopped at and gets only
what was appended since.

Usage:  nightshift talk            start and open a browser
        nightshift talk --port 9000
        nightshift talk --no-open
"""
import glob, json, os, re, sys, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import (collect, focus_pane, send_pane, send_key, screen_of, mark_seen, restart,
                   ago, PROJ, HOME)

HERE = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE, "talk.html")
FONTS = os.path.join(HERE, "fonts")         # JetBrains Mono, OFL - see fonts/OFL.txt
FONT_RE = re.compile(r"^/fonts/(jetbrains-mono-[a-z-]+\.woff2)$")

SID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TAG_RE = re.compile(r"<(system-reminder|local-command-caveat|local-command-stdout"
                    r"|command-message|command-contents)>.*?</\1>", re.S)
CMD_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
ARG_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)

TAIL_BYTES = 400_000        # first load reads only the end of a long transcript
MAX_TAIL = 6_000_000        # ... but keeps widening, up to here, to find events
MIN_EVENTS = 80             # a single base64 record can fill a whole window
IN_CAP = 4_000              # per tool-input value
OUT_CAP = 8_000             # per tool result
# records that are bookkeeping, not conversation
SKIP_TYPES = {"mode", "bridge-session", "file-history-snapshot", "last-prompt",
              "atis-latch", "ai-title", "attachment", "summary"}


def _clip(s, n):
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "\n… [%d more chars]" % (len(s) - n)


def _text_of(block):
    """tool_result content is a string, or a list of blocks."""
    c = block.get("content")
    if isinstance(c, str):
        return c, 0
    imgs, out = 0, []
    if isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "image":
                imgs += 1
            elif b.get("type") == "tool_reference":
                out.append("→ %s" % b.get("tool_name", "?"))
    return "\n".join(out), imgs


def events_from(lines):
    """One transcript line -> zero or more things worth showing."""
    ev = []
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        t = o.get("type")
        if t == "queue-operation":
            # Typed while it was working: Claude Code holds the message in a queue
            # (shown under its prompt) and writes it as a turn only once it is
            # picked up. Every operation goes to the browser, background-task
            # notifications included, so its copy of the queue stays in step.
            op, txt = o.get("operation"), o.get("content")
            txt = txt.strip()[:OUT_CAP] if isinstance(txt, str) else ""
            if op in ("enqueue", "remove", "dequeue"):
                ev.append(dict(k="queue", op=op, ts=o.get("timestamp") or "", text=txt,
                               human=bool(txt) and not txt.startswith("<"),
                               taken=o.get("reason") == "absorbed_mid_turn"))
            continue
        if t == "attachment":
            a = o.get("attachment") or {}
            origin = a.get("origin") or {}
            if a.get("type") == "queued_command" and a.get("commandMode") == "prompt" \
                    and isinstance(origin, dict) and origin.get("kind") == "human":
                body = TAG_RE.sub("", a.get("prompt") or "").strip()
                if body:                         # a message it took in mid-turn
                    ev.append(dict(k="user", ts=a.get("timestamp") or o.get("timestamp") or "",
                                   side=False, text=body, mid=True))
            continue
        if t in SKIP_TYPES:
            continue
        ts = o.get("timestamp") or ""
        side = bool(o.get("isSidechain"))
        msg = o.get("message") or {}
        cont = msg.get("content")

        if t == "system":
            body = TAG_RE.sub("", o.get("content") or "").strip()
            if body:
                ev.append(dict(k="note", ts=ts, side=side, text=_clip(body, OUT_CAP)))
            continue

        if t == "user":
            if isinstance(cont, str):
                cmd = CMD_RE.search(cont)
                if cmd:
                    args = ARG_RE.search(cont)
                    ev.append(dict(k="cmd", ts=ts, side=side,
                                   text=cmd.group(1).strip(),
                                   args=(args.group(1).strip() if args else "")))
                    continue
                body = TAG_RE.sub("", cont).strip()
                if body and not o.get("isMeta"):
                    ev.append(dict(k="user", ts=ts, side=side, text=body))
                continue
            if isinstance(cont, list):
                for b in cont:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        txt, imgs = _text_of(b)
                        ev.append(dict(k="result", ts=ts, side=side,
                                       id=b.get("tool_use_id", ""),
                                       ok=not b.get("is_error"),
                                       imgs=imgs, text=_clip(txt, OUT_CAP)))
                    elif b.get("type") == "text":
                        body = TAG_RE.sub("", b.get("text", "")).strip()
                        if body and not o.get("isMeta"):
                            ev.append(dict(k="user", ts=ts, side=side, text=body))
                    elif b.get("type") == "image":
                        ev.append(dict(k="user", ts=ts, side=side, text="[image]"))
            continue

        if t == "assistant" and isinstance(cont, list):
            for b in cont:
                if not isinstance(b, dict):
                    continue
                kind = b.get("type")
                if kind == "text" and b.get("text", "").strip():
                    ev.append(dict(k="say", ts=ts, side=side, text=b["text"]))
                elif kind == "thinking":
                    # Claude Code stores only the encrypted signature, so the
                    # text is nearly always empty - show that a pause happened.
                    txt = b.get("thinking", "")
                    ev.append(dict(k="think", ts=ts, side=side, text=txt,
                                   sealed=not txt.strip()))
                elif kind == "tool_use":
                    inp = b.get("input") or {}
                    if isinstance(inp, dict):
                        inp = {k: (_clip(v, IN_CAP) if isinstance(v, str) else v)
                               for k, v in inp.items()}
                    ev.append(dict(k="tool", ts=ts, side=side, id=b.get("id", ""),
                                   name=b.get("name", "tool"), input=inp))
    return ev


def _slice(path, start, size):
    """Complete lines from `start` to EOF, and the offset just past them."""
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    if start > 0:                            # never hand back half a line
        nl = data.find(b"\n")
        if nl < 0:
            return [], size
        start, data = start + nl + 1, data[nl + 1:]
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], start
    body = data[:cut + 1]
    return body.decode("utf-8", "replace").splitlines(), start + len(body)


def tail(path, start=None):
    """Events appended since byte `start`, + the offset to ask from next time.

    start=None means "the tail of the conversation". That window widens until it
    holds a useful number of events: one pasted image or one huge tool result can
    be bigger than the whole byte window, and an empty screen is not an answer."""
    size = os.path.getsize(path)
    reset = start is not None and start > size   # file replaced or truncated
    if reset:
        start = 0
    if start is not None:
        lines, nxt = _slice(path, start, size)
        return events_from(lines), nxt, size, False, reset
    win = TAIL_BYTES
    while True:
        begin = max(0, size - win)
        lines, nxt = _slice(path, begin, size)
        ev = events_from(lines)
        if len(ev) >= MIN_EVENTS or begin == 0 or win >= MAX_TAIL:
            return ev, nxt, size, begin > 0, reset
        win *= 4


_pcache = {}


def probe(path):
    """(title, cwd) for a transcript, from its tail. Cached on mtime."""
    m = os.path.getmtime(path)
    hit = _pcache.get(path)
    if hit and hit[0] == m:
        return hit[1]
    title = cwd = ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
        prompt = ""
        for ln in reversed(lines):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            title = title or (o.get("aiTitle") or "")
            prompt = prompt or (o.get("lastPrompt") or "")
            cwd = cwd or (o.get("cwd") or "")
            if title and cwd:
                break
        title = title or " ".join(prompt.split())[:60]
    except Exception:
        pass
    out = (title, cwd.replace(HOME, "~"))
    _pcache[path] = (m, out)
    return out


# Claude Code hands its status line command the numbers it shows at the bottom
# of the terminal - context used, the 5h and 7d limits, cost - as JSON on stdin,
# and nowhere else. Three lines in ~/.claude/statusline-command.sh keep a copy per
# session here (see the README), which is what lets talk show the same numbers.
STATUS_DIR = os.path.join(HOME, ".claude", "nightshift-status")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
_scache, _ccache = {}, {}


def _status_of(sid):
    """(what the status line last got for this session, when) or ({}, 0)."""
    p = os.path.join(STATUS_DIR, sid + ".json")
    try:
        m = os.path.getmtime(p)
    except OSError:
        return {}, 0
    hit = _scache.get(p)
    if hit and hit[0] == m:
        return hit[1], m
    try:
        with open(p) as f:
            d = json.load(f)
    except Exception:
        d = {}
    _scache[p] = (m, d if isinstance(d, dict) else {})
    return _scache[p][1], m


def _context_from(path):
    """(tokens in context, model id) from the newest main-thread reply's usage -
    the fallback for a session whose status line has not run since we started
    keeping copies. Cached on mtime."""
    try:
        m = os.path.getmtime(path)
    except OSError:
        return 0, ""
    hit = _ccache.get(path)
    if hit and hit[0] == m:
        return hit[1]
    out = (0, "")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 524288))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for ln in reversed(lines):
            if '"compact_boundary"' in ln:       # compacted since its last reply: the
                break                            # count before it no longer applies
            if '"usage"' not in ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            msg = o.get("message") or {}
            u = msg.get("usage") or {}
            if o.get("type") != "assistant" or o.get("isSidechain") or not u \
                    or msg.get("model") == "<synthetic>":
                continue
            out = (sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens")),
                   msg.get("model") or "")
            break
    except OSError:
        pass
    _ccache[path] = (m, out)
    return out


def _default_1m():
    try:
        with open(SETTINGS) as f:
            return "[1m]" in (json.load(f).get("model") or "")
    except Exception:
        return False


def usage(sid, path):
    """This session's context and cost, the way its status line shows them."""
    d, m = _status_of(sid)
    cw = d.get("context_window") or {}
    if cw.get("used_percentage") is not None:
        cu = cw.get("current_usage") or {}
        used = cw.get("total_input_tokens") or sum(
            cu.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                     "cache_read_input_tokens"))
        return dict(src="statusline", at=int(m * 1000), pct=cw["used_percentage"],
                    used=used, size=cw.get("context_window_size") or 0,
                    model=(d.get("model") or {}).get("display_name") or "",
                    cost=(d.get("cost") or {}).get("total_cost_usd"),
                    effort=(d.get("effort") or {}).get("level") or "")
    if not path:
        return None
    tok, model = _context_from(path)
    if not tok:
        return None
    # the transcript does not say which window the model had - [1m] is dropped
    size = 1_000_000 if tok > 200_000 or _default_1m() else 200_000
    return dict(src="transcript", at=0, pct=round(tok * 100.0 / size), used=tok,
                size=size, model=model, cost=None, effort="")


def limits():
    """The 5h and 7d limits are the account's, not a session's: take them from
    whichever status line ran last. Copies untouched for two weeks are dropped."""
    best, when = {}, 0
    cutoff = time.time() - 14 * 86400
    for p in glob.glob(os.path.join(STATUS_DIR, "*.json")):
        sid = os.path.basename(p)[:-5]
        d, m = _status_of(sid)
        if m and m < cutoff:
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if m > when and d.get("rate_limits"):
            best, when = d["rate_limits"], m
    pick = lambda k: {"pct": (best.get(k) or {}).get("used_percentage"),
                      "resets": (best.get(k) or {}).get("resets_at")} if best.get(k) else None
    return dict(at=int(when * 1000), five=pick("five_hour"), week=pick("seven_day")) \
        if best else None


def sessions():
    """Live sessions from the registry, then recent transcripts that have ended."""
    rows, seen = [], set()
    for r in collect():
        path = os.path.join(PROJ, "*", (r["sid"] or "-") + ".jsonl")
        hits = glob.glob(path)
        if r["sid"]:
            seen.add(r["sid"])
        title, _ = probe(hits[0]) if hits else ("", "")
        rows.append(dict(r, live=True, title=title, has=bool(hits),
                         usage=usage(r["sid"], hits[0] if hits else "") if r["sid"] else None))
    ended = []
    for p in glob.glob(os.path.join(PROJ, "*", "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        if sid in seen or not SID_RE.match(sid):
            continue
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        ended.append((m, sid, p))
    ended.sort(reverse=True)
    for m, sid, p in ended[:40]:
        title, cwd = probe(p)
        rows.append(dict(name=title or sid[:8], sid=sid, state="ended", live=False,
                         title=title, has=True, draft="", pane="", where="-",
                         cwd=cwd, started=None, touched=int(m * 1000),
                         quiet=int(max(0, time.time() - m)), age="-",
                         quiet_str=ago(m * 1000), snip="", model=""))
    return rows


# Claude Code's own slash commands, checked against 2.1.283. The transcript never
# lists these - only skills - so they are kept by hand. `ui` = opens a panel in
# the pane rather than printing, so talk opens the terminal view to show it.
BUILTINS = [
    ("add-dir", "Add a new working directory", 0),
    ("artifacts", "Browse your published and shared artifacts", 1),
    ("branch", "Create a branch of the current conversation at this point", 0),
    ("btw", "Ask a quick side question without interrupting the main conversation", 0),
    ("clear", "Start a new session with empty context; the old one stays resumable", 0),
    ("compact", "Free up context by summarizing the conversation so far", 0),
    ("config", "Open settings", 1),
    ("context", "Visualize current context usage as a colored grid", 0),
    ("copy", "Copy Claude's last response to clipboard (or /copy N for the Nth-latest)", 0),
    ("doctor", "Check the health of this Claude Code install", 1),
    ("effort", "Set effort level for model usage", 1),
    ("exit", "Exit Claude Code - this ends the session", 0),
    ("export", "Export the current conversation to a file or clipboard", 1),
    ("fast", "Toggle fast mode", 0),
    ("help", "Show help and available commands", 1),
    ("hooks", "View hook configurations for tool events", 1),
    ("ide", "Manage IDE integrations and show status", 1),
    ("init", "Initialize a CLAUDE.md file with codebase documentation", 0),
    ("loops", "List, create, and delete loops", 1),
    ("mcp", "Manage MCP servers", 1),
    ("memory", "Edit CLAUDE.md files and memory settings", 1),
    ("model", "Set the AI model for Claude Code", 1),
    ("output-style", "List output styles or switch to one", 1),
    ("permissions", "Manage allow and deny tool permission rules", 1),
    ("plan", "Enable plan mode or view the current session plan", 0),
    ("plugin", "Manage Claude Code plugins", 1),
    ("recap", "Generate a one-line session recap now", 0),
    ("release-notes", "Show what changed in recent versions", 1),
    ("reload-plugins", "Activate pending plugin changes in the current session", 0),
    ("reload-skills", "Pick up skills added or changed on disk during this session", 0),
    ("resume", "Resume a previous conversation", 1),
    ("rewind", "Rewind the conversation and code to an earlier point", 1),
    ("skills", "List available skills", 1),
    ("status", "Show version, model, account, API connectivity and tool statuses", 1),
    ("tasks", "View and manage everything running in the background", 1),
    ("theme", "Change the theme", 1),
    ("todos", "Show the current todo list", 0),
    ("usage", "Show session cost, plan usage, and activity stats", 1),
]
_cmds = {}                  # transcript path -> [offset scanned to, skill rows]


def commands(sid):
    """What `/` can complete to in this session: the built-ins, then the skills
    and custom commands Claude Code listed for it. That list is a `skill_listing`
    attachment in the transcript - the exact set the session loaded, plugin and
    project skills included - and a later one (after /reload-skills) replaces
    it. Scanned incrementally, since it can sit a megabyte into the file."""
    out = [dict(name=n, desc=d, kind="built-in", ui=bool(u)) for n, d, u in BUILTINS]
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl")) if SID_RE.match(sid or "") else []
    if not hits:
        return out
    path = hits[0]
    off, rows = _cmds.get(path, [0, []])
    try:
        if os.path.getsize(path) < off:          # rewritten under us: start over
            off, rows = 0, []
        with open(path, "rb") as f:
            f.seek(off)
            for ln in f:
                if not ln.endswith(b"\n"):       # half-written line: next time
                    break
                off += len(ln)
                if b'"skill_listing"' not in ln:
                    continue
                try:
                    a = json.loads(ln).get("attachment") or {}
                except ValueError:
                    continue
                if a.get("type") != "skill_listing":
                    continue
                rows = []
                for item in (a.get("content") or "").splitlines():
                    # "- codex:rescue: Delegate ..." - the name can hold colons too
                    m = re.match(r"^- (\S+?)(?::\s+(.*))?:?$", item)
                    if m:
                        d = m.group(2) or ""
                        rows.append(dict(name=m.group(1), kind="skill", ui=False,
                                         desc=d if len(d) <= 300 else d[:299] + "\u2026"))
    except OSError:
        return out
    _cmds[path] = [off, rows]
    have = {c["name"] for c in out}
    return out + [r for r in rows if r["name"] not in have]


IMG_TYPES = {"image/png": "png", "image/jpeg": "jpg",
             "image/gif": "gif", "image/webp": "webp"}
UPLOAD_CAP = 50 * 1024 * 1024
NAME_RE = re.compile(r"[^\w.\-()\[\]+@,]")      # no spaces: a path in a message stays one word
PASTE_DIR = os.path.join(HOME, ".claude", "nightshift-paste")


def _row_for(sid):
    """The live session with this id, or None. The browser never names a pane."""
    if not SID_RE.match(sid or ""):
        return None
    for r in collect():
        if r["sid"] == sid:
            return r
    return None


def _keep_image(data, ext, name=""):
    """Park a pasted image or file on disk and return its path - a terminal cannot
    carry bytes, but Claude Code can read a file. A file keeps its own name behind
    the timestamp; a screenshot has none worth keeping. Older than a week goes."""
    os.makedirs(PASTE_DIR, exist_ok=True)
    cutoff = time.time() - 7 * 86400
    for old in glob.glob(os.path.join(PASTE_DIR, "*")):
        try:
            if os.path.getmtime(old) < cutoff:
                os.remove(old)
        except OSError:
            pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = "%s-%s" % (stamp, name) if name else "%s.%s" % (stamp, ext)
    path = os.path.join(PASTE_DIR, base)
    n = 1
    while os.path.exists(path):
        stem, dot, tail = base.rpartition(".")
        path = os.path.join(PASTE_DIR, "%s-%d.%s" % (stem, n, tail) if dot
                            else "%s-%d" % (base, n))
        n += 1
    with open(path, "wb") as f:
        f.write(data)
    return path


class TalkRoutes:
    """Talk's routes, mixed into both servers.

    The office and talk are two views of the same registry, so `nightshift`
    serves both on one port: the office at /, talk at /talk. Namespaced under
    /api/talk/ because
    the office's own /api/sessions must keep returning live sessions only - the
    talk list also carries ended ones, and those have no desk to sit at."""

    def talk_get(self, path, q):
        if path in ("/talk", "/talk/"):
            try:
                with open(PAGE, "rb") as f:      # re-read so edits are live
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, "cannot read %s: %s" % (PAGE, e), "text/plain")
            return True
        m = FONT_RE.match(path)                  # bundled, so talk works offline
        if m:
            try:
                with open(os.path.join(FONTS, m.group(1)), "rb") as f:
                    self._send(200, f.read(), "font/woff2")
            except OSError:
                self._send(404, "no such font", "text/plain")
            return True
        if path == "/api/talk/sessions":
            self._json(200, {"now": int(time.time() * 1000), "sessions": sessions(),
                             "limits": limits()})
            return True
        if path == "/api/talk/transcript":
            sid = q.get("sid", "")
            if not SID_RE.match(sid):
                self._json(400, {"error": "bad session id"})
                return True
            hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
            if not hits:
                self._json(404, {"error": "no transcript"})
                return True
            start = None
            if "from" in q:
                try:
                    start = max(0, int(q["from"]))
                except ValueError:
                    start = None
            try:
                ev, nxt, size, trimmed, reset = tail(hits[0], start)
            except OSError as e:
                self._json(500, {"error": str(e)})
                return True
            title, cwd = probe(hits[0])
            self._json(200, dict(events=ev, next=nxt, size=size, trimmed=trimmed,
                                 reset=reset, title=title, cwd=cwd))
            return True
        if path == "/api/talk/commands":              # what `/` completes to
            self._json(200, {"commands": commands(q.get("sid", ""))})
            return True
        if path == "/api/talk/screen":                # what that pane shows now
            row = _row_for(q.get("sid", ""))
            if not row or not row["pane"]:
                self._json(200, {"screen": "", "live": False})
                return True
            self._json(200, {"screen": screen_of(row["pane"], 14), "live": True,
                             "state": row["state"]})
            return True
        return False

    def talk_post(self, path, body, ctype=""):
        """POST routes. `body` is raw bytes - JSON for send, image bytes for upload."""
        if path == "/api/talk/send":
            try:
                d = json.loads(body or b"{}")
            except Exception:
                self._json(400, {"error": "bad body"})
                return True
            row = _row_for(d.get("sid") or "")
            if not row:
                self._json(400, {"error": "unknown session"})
                return True
            if not row["pane"]:
                self._json(400, {"error": "that session is not in tmux or herdr"})
                return True
            key = d.get("key") or ""
            if key:
                err = send_key(row["pane"], key)
                self._json(400 if err else 200, {"error": err} if err else {"ok": True})
                return True
            text = d.get("text") or ""
            submit = bool(d.get("submit", True))
            # a session that is blocked on a prompt may have a dialog on screen, and
            # Enter would answer it - so the first press only types, and committing
            # takes a second, deliberate one.
            # Decided here, on the state as it is now - the page's copy can be a
            # poll old, and a stale "waiting" there left messages typed but unsent.
            if submit and row["state"] == "waiting" and not d.get("confirm"):
                if not text:
                    self._json(409, {"error": "waiting", "state": "waiting"})
                    return True
                submit = False
            err = send_pane(row["pane"], text, submit)
            self._json(400 if err else 200,
                       {"error": err} if err else {"ok": True, "submitted": submit})
            return True
        if path == "/api/talk/answer":                # a question Claude asked
            from . import ask
            try:
                d = json.loads(body or b"{}")
            except Exception:
                self._json(400, {"error": "bad body"})
                return True
            err = ask.answer(d.get("sid") or "", d.get("id") or "", d.get("picks"))
            self._json(409 if err else 200, {"error": err} if err else {"ok": True})
            return True
        if path == "/api/talk/restart":               # "Restart to update", from here
            try:
                d = json.loads(body or b"{}")
            except Exception:
                self._json(400, {"error": "bad body"})
                return True
            err = restart(d.get("sid") or "")
            self._json(409 if err else 200, {"error": err} if err else {"ok": True})
            return True
        if path == "/api/talk/seen":                  # you have read this far
            try:
                d = json.loads(body or b"{}")
            except Exception:
                self._json(400, {"error": "bad body"})
                return True
            sid = d.get("sid") or ""
            if not SID_RE.match(sid):
                self._json(400, {"error": "bad session id"})
                return True
            err = mark_seen(sid)
            self._json(500 if err else 200, {"error": err} if err else {"ok": True})
            return True
        if path == "/api/talk/upload":
            # the browser never hands over a file's real path, so it comes as bytes
            # plus the name it had; a clipboard screenshot is just "image.png"
            from urllib.parse import unquote
            name = NAME_RE.sub("_", os.path.basename(unquote(
                self.headers.get("X-Filename") or ""))).lstrip(".")[-120:]
            ext = IMG_TYPES.get((ctype or "").split(";")[0].strip())
            if re.fullmatch(r"image\.(png|jpe?g|gif|webp)", name or "", re.I):
                name = ""
            if not ext and not name:
                name = "paste.bin"
            if len(body) > UPLOAD_CAP:
                self._json(413, {"error": "over 50 MB"})
                return True
            try:
                path_ = _keep_image(body, ext or "bin", name)
            except OSError as e:
                self._json(500, {"error": str(e)})
                return True
            self._json(200, {"path": path_, "short": path_.replace(HOME, "~")})
            return True
        return False


class Handler(TalkRoutes, BaseHTTPRequestHandler):
    server_version = "nightshift-talk"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/":
            path = "/talk"                       # standalone: talk is home
        if self.talk_get(path, q):
            return
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, UPLOAD_CAP + 1024)) if n else b""
        if self.talk_post(path, raw, self.headers.get("Content-Type", "")):
            return
        if path != "/api/focus":
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._json(400, {"error": "bad body"})
        pane = body.get("pane") or ""
        err = focus_pane(pane)
        if err:
            return self._json(400 if "pane" in err else 500, {"error": err})
        return self._json(200, {"ok": True, "pane": pane})


def main():
    args = sys.argv[1:]
    port = 8788
    if "--port" in args:
        try: port = int(args[args.index("--port") + 1])
        except Exception: pass
    url = "http://localhost:%d" % port
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print("nightshift talk: cannot bind %s (%s)" % (url, e)); sys.exit(1)
    print("nightshift talk serving %s  (ctrl-c to stop)" % url)
    if "--no-open" not in args:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nnightshift talk stopped")


if __name__ == "__main__":
    main()
