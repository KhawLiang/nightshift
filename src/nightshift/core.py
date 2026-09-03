"""Shared session-discovery for the terminal board and the pixel office.

Everything comes from the registry Claude Code maintains at ~/.claude/sessions/,
plus a peek at each tmux pane for input typed but never submitted.
"""
import json, os, re, time, glob, subprocess

from . import herdr

HOME = os.path.expanduser("~")
SESS = os.path.join(HOME, ".claude", "sessions")
PROJ = os.path.join(HOME, ".claude", "projects")

RANK = dict(waiting=0, draft=1, idle=2, busy=3, unknown=4)
SEEN = os.path.join(HOME, ".claude", "nightshift-seen.json")
_seen = {"t": 0.0, "map": {}}
_focus = {"sid": "", "t": 0.0}
PANE_RE = re.compile(r"^%\d+$")


def seen_map():
    """{sessionId: when you last looked at it, ms}. Claude Code has no notion of
    read/unread, so this is ours: talk stamps a session when you open it, and the
    office stamps one when you jump to its pane."""
    try:
        m = os.path.getmtime(SEEN)
    except OSError:
        return _seen["map"] if _seen["t"] else {}
    if m != _seen["t"]:
        try:
            with open(SEEN) as f:
                _seen["map"] = {k: int(v) for k, v in json.load(f).items()}
            _seen["t"] = m
        except Exception:
            pass
    return _seen["map"]


def mark_seen(sid, when=None):
    """Remember that you have now read this far. '' on success, else why not."""
    if not sid:
        return "no session id"
    m = dict(seen_map())
    m[sid] = int(when or time.time() * 1000)
    if len(m) > 400:                             # keep the newest few hundred
        m = dict(sorted(m.items(), key=lambda kv: kv[1], reverse=True)[:400])
    try:
        os.makedirs(os.path.dirname(SEEN), exist_ok=True)
        tmp = SEEN + ".tmp"
        with open(tmp, "w") as f:
            json.dump(m, f)
        os.replace(tmp, SEEN)
    except OSError as e:
        return str(e)
    _seen["map"], _seen["t"] = m, os.path.getmtime(SEEN)
    return ""


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, TypeError):
        return False


def ago(ms):
    if not ms:
        return "-"
    d = max(0, time.time() - ms / 1000.0)
    if d < 60:    return "%ds" % int(d)
    if d < 3600:  return "%dm" % int(d // 60)
    if d < 86400: return "%dh%02dm" % (d // 3600, (d % 3600) // 60)
    return "%dd" % int(d // 86400)


def _tmux(*args, timeout=1.5):
    try:
        r = subprocess.run(("tmux",) + args, capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def transcript_path(sid):
    """Where Claude Code keeps this session's conversation, or ''."""
    if not sid:
        return ""
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
    return hits[0] if hits else ""


_tcache = {}


def transcript(sid):
    """(mtime, last-activity-snippet) for a session, cached on mtime."""
    if not sid:
        return (0, "")
    p = transcript_path(sid)
    if not p:
        return (0, "")
    try:
        m = os.path.getmtime(p)
    except OSError:
        return (0, "")
    c = _tcache.get(p)
    if c and c[0] == m:
        return (m, c[1])
    txt = ""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for ln in reversed(lines):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            msg = o.get("message") or {}
            role = msg.get("role") or o.get("type")
            cont = msg.get("content")
            piece = ""
            if isinstance(cont, str):
                piece = cont
            elif isinstance(cont, list):
                for b in cont:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            piece = b.get("text", "")
                        elif b.get("type") == "tool_use":
                            piece = "[%s] %s" % (b.get("name", "tool"),
                                                 str(b.get("input", {}))[:70])
                    if piece:
                        break
            piece = " ".join(piece.split())
            if piece and not piece.startswith("<"):
                txt = ("%s› " % (role or "?")[:1]) + piece
                break
    except Exception:
        pass
    _tcache[p] = (m, txt)
    return (m, txt)


def pane_id(tmux):
    """'new88:@14.%20' -> '%20'."""
    if not tmux or "." not in tmux:
        return ""
    return tmux.rsplit(".", 1)[-1]


def _draft_in(txt):
    """The prompt line of a captured screen, if something is sitting in it."""
    for ln in reversed(txt.splitlines()[-30:]):
        s = ln.strip()
        for mark in ("❯", ">"):
            if s.startswith(mark):
                rest = s[len(mark):].strip()
                return rest if rest and not rest.startswith("─") else ""
    return ""


def pane_draft(pane, mux="tmux"):
    """Text sitting in the prompt box, typed but never submitted."""
    if not pane:
        return ""
    if mux == "herdr":
        return _draft_in(herdr.read(pane))
    return _draft_in(_tmux("capture-pane", "-p", "-t", pane))


def where(tmux):
    """'new88:@14.%20' -> 'new88:1', or 'new88:0.1' when that window holds more
    than one pane - two agents can share a split window, and the label has to
    tell them apart."""
    if not tmux:
        return "-"
    sess = tmux.split(":")[0]
    pane = pane_id(tmux)
    if not pane:
        return sess
    out = _tmux("display", "-p", "-t", pane,
                "#{window_index}\t#{pane_index}\t#{window_panes}").strip()
    parts = out.split("\t")
    if len(parts) != 3 or not parts[0]:
        return sess
    win, pidx, npanes = parts
    try:
        multi = int(npanes) > 1
    except ValueError:
        multi = False
    return "%s:%s.%s" % (sess, win, pidx) if multi else "%s:%s" % (sess, win)


def collect():
    rows = []
    for f in glob.glob(os.path.join(SESS, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not alive(d.get("pid")):
            continue
        tmux = d.get("tmux") or ""
        pane, mux, spot = pane_id(tmux), "tmux", ""
        if not pane and herdr.available():
            # herdr is its own runtime, so the registry has no pane to hand us -
            # claim one by walking the session's process ancestry.
            pane, spot = herdr.pane_for(d.get("pid"))
            mux = "herdr" if pane else ""
        st = (d.get("status") or "unknown").lower()
        if st not in RANK:
            st = "unknown"
        kind = d.get("kind") or "interactive"
        draft = ""
        # Only a stopped session holds a draft, and only an interactive one has a
        # prompt box at all: background agents share their parent's pane, and
        # would otherwise inherit - and mis-report - its unsent text.
        if st in ("idle", "unknown") and kind == "interactive":
            draft = pane_draft(pane, mux)
            if draft:
                st = "draft"
        tm, snip = transcript(d.get("sessionId", ""))
        touched = int(max(d.get("statusUpdatedAt") or 0,
                          d.get("updatedAt") or 0, tm * 1000))
        rows.append(dict(
            name=d.get("name") or "?",
            sid=d.get("sessionId") or "",
            kind=kind,
            state=st,
            draft=draft,
            pane=pane,
            mux=mux,
            where=spot or where(tmux),
            cwd=(d.get("cwd") or "").replace(HOME, "~"),
            started=d.get("startedAt"),
            touched=touched,
            quiet=int(max(0, time.time() - touched / 1000.0)) if touched else 0,
            age=ago(d.get("startedAt")),
            quiet_str=ago(touched),
            snip=snip,
            model=d.get("model") or "",
        ))
    seen = seen_map()
    for r in rows:                               # anything written since you looked
        r["unread"] = bool(r["touched"]) and r["touched"] > seen.get(r["sid"], 0)
    # the pane you have focused is one you are looking at, so it is never unread -
    # stamped to disk when the focus moves, or once every half minute, not every poll
    watching = herdr.current()[1] if herdr.available() else ""
    if watching:
        for r in rows:
            if r["sid"] == watching:
                r["unread"] = False
        now = time.time()
        if watching != _focus["sid"] or now - _focus["t"] > 30:
            mark_seen(watching)
            _focus["sid"], _focus["t"] = watching, now
    rows.sort(key=lambda r: (RANK[r["state"]], r["name"]))
    return rows


def interactive(rows):
    """Only the sessions you can actually talk to.

    Background agents (`kind: "bg"`) are spawned by an interactive session and
    share its pane - Claude Code keeps spare ones warm - so they have no prompt
    of their own and nothing to click through to. They are still in `collect()`,
    and the reader still lists them; they just do not get a desk.
    """
    return [r for r in rows if r.get("kind") == "interactive"]


KEYS = {                                   # the only key presses we will ever send
    "esc":    ("esc",    "Escape"),        # herdr name, tmux name
    "ctrl-c": ("ctrl+c", "C-c"),           # herdr spells this one with a plus
    "up":     ("up",     "Up"),
    "enter":  ("enter",  "Enter"),
}
TEXT_CAP = 8192


def _live_pane(pane):
    """The row for a pane we are tracking right now, or None. Same gate as focus."""
    shaped = isinstance(pane, str) and (PANE_RE.match(pane) or
                                        (herdr.available() and herdr.ID_RE.match(pane)))
    if not shaped:
        return None
    return {s["pane"]: s for s in collect() if s["pane"]}.get(pane)


def send_pane(pane, text="", submit=True):
    """Type `text` into a pane, optionally pressing Enter. '' on success, else why not.

    The counterpart to focus_pane: same narrow gate (a pane id we are tracking),
    and the text is always one argv item - never a shell string.
    """
    row = _live_pane(pane)
    if row is None:
        return "unknown pane"
    text = (text or "")[:TEXT_CAP]
    if not text and not submit:
        return ""
    if row["mux"] == "herdr":
        return herdr.send(pane, text, ["enter"] if submit else [])
    try:
        if text:
            # a buffer + bracketed paste, so a multi-line message stays one prompt
            subprocess.run(["tmux", "set-buffer", "-b", "nightshift", "--", text],
                           capture_output=True, timeout=2, check=True)
            subprocess.run(["tmux", "paste-buffer", "-b", "nightshift", "-d", "-p",
                            "-t", pane], capture_output=True, timeout=2, check=True)
        if submit:
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"],
                           capture_output=True, timeout=2, check=True)
    except Exception as e:
        return str(e)
    return ""


def send_key(pane, key):
    """Press one key from KEYS in a pane. '' on success, else why not."""
    if key not in KEYS:
        return "key not allowed"
    row = _live_pane(pane)
    if row is None:
        return "unknown pane"
    hk, tk = KEYS[key]
    if row["mux"] == "herdr":
        return herdr.send(pane, "", [hk])
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, tk],
                       capture_output=True, timeout=2, check=True)
    except Exception as e:
        return str(e)
    return ""


def screen_of(pane, lines=14):
    """What that pane is showing right now, as plain text ('' if we cannot look)."""
    row = _live_pane(pane)
    if row is None:
        return ""
    if row["mux"] == "herdr":
        out = herdr.read(pane, lines)
    else:
        out = _tmux("capture-pane", "-p", "-t", pane, "-S", "-%d" % lines)
    rows = [r.rstrip() for r in (out or "").splitlines()]
    while rows and not rows[0]:                 # a pane is mostly empty space
        rows.pop(0)
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows[-lines:])


def focus_pane(pane):
    """Jump the terminal to a pane. Returns '' on success, else why not.

    Deliberately narrow: the pane must look like a pane id *and* be one we are
    tracking right now, and only select-window/select-pane (or `herdr pane
    focus`) ever run - never send-keys, never a shell string."""
    shaped = isinstance(pane, str) and (PANE_RE.match(pane) or
                                        (herdr.available() and herdr.ID_RE.match(pane)))
    if not shaped:
        return "not a pane id"
    rows = {s["pane"]: s for s in collect() if s["pane"]}
    if pane not in rows:
        return "unknown pane"
    if rows[pane]["mux"] == "herdr":
        return herdr.focus(pane)
    if not PANE_RE.match(pane):
        return "not a pane id"
    try:
        for cmd in ("select-window", "select-pane"):
            subprocess.run(["tmux", cmd, "-t", pane],
                           capture_output=True, timeout=2, check=True)
    except Exception as e:
        return str(e)
    return ""
