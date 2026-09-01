#!/usr/bin/env python3
"""nightshift read - read what any Claude Code session on this Mac is saying.

The office shows *state*; this shows *content*. Left: every session, live ones
first. Right: that session's conversation, tailed as it is written.

Claude Code appends one JSON object per line to
~/.claude/projects/<slug>/<sessionId>.jsonl, so following a conversation is a
byte-offset tail - the browser sends back the offset it stopped at and gets only
what was appended since.

Usage:  nightshift read            start and open a browser
        nightshift read --port 9000
        nightshift read --no-open
"""
import glob, json, os, re, sys, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import collect, focus_pane, ago, PROJ, HOME

HERE = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE, "read.html")

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


def sessions():
    """Live sessions from the registry, then recent transcripts that have ended."""
    rows, seen = [], set()
    for r in collect():
        path = os.path.join(PROJ, "*", (r["sid"] or "-") + ".jsonl")
        hits = glob.glob(path)
        if r["sid"]:
            seen.add(r["sid"])
        title, _ = probe(hits[0]) if hits else ("", "")
        rows.append(dict(r, live=True, title=title, has=bool(hits)))
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


class Handler(BaseHTTPRequestHandler):
    server_version = "nightshift-read"

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
            try:
                with open(PAGE, "rb") as f:     # re-read so edits are live
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, "cannot read %s: %s" % (PAGE, e), "text/plain")
        if path == "/api/sessions":
            return self._json(200, {"now": int(time.time() * 1000),
                                    "sessions": sessions()})
        if path == "/api/transcript":
            sid = q.get("sid", "")
            if not SID_RE.match(sid):
                return self._json(400, {"error": "bad session id"})
            hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
            if not hits:
                return self._json(404, {"error": "no transcript"})
            start = None
            if "from" in q:
                try:
                    start = max(0, int(q["from"]))
                except ValueError:
                    start = None
            try:
                ev, nxt, size, trimmed, reset = tail(hits[0], start)
            except OSError as e:
                return self._json(500, {"error": str(e)})
            title, cwd = probe(hits[0])
            return self._json(200, dict(events=ev, next=nxt, size=size,
                                        trimmed=trimmed, reset=reset,
                                        title=title, cwd=cwd))
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/focus":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
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
        print("nightshift read: cannot bind %s (%s)" % (url, e)); sys.exit(1)
    print("nightshift read serving %s  (ctrl-c to stop)" % url)
    if "--no-open" not in args:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nnightshift read stopped")


if __name__ == "__main__":
    main()
