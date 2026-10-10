"""Shared session-discovery for the pixel office and talk.

Everything comes from the registry Claude Code maintains at ~/.claude/sessions/,
plus a peek at each tmux pane for input typed but never submitted.
"""
import calendar, json, os, re, shlex, time, glob, subprocess

from . import herdr

HOME = os.path.expanduser("~")
SESS = os.path.join(HOME, ".claude", "sessions")
PROJ = os.path.join(HOME, ".claude", "projects")

RANK = dict(waiting=0, draft=1, idle=2, busy=3, unknown=4)
SEEN = os.path.join(HOME, ".claude", "nightshift-seen.json")
_seen = {"t": 0.0, "map": {}}
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


_inst = {"key": None, "v": ""}
VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def installed():
    """The Claude Code version a restart would run: what `claude` resolves to
    now. The native installer keeps one file per version and points `claude` at
    the newest, so its name is the answer; otherwise ask it, once per change."""
    import shutil
    cmd = shutil.which("claude") or os.path.join(HOME, ".local", "bin", "claude")
    try:
        real = os.path.realpath(cmd)
        key = (real, os.path.getmtime(real))
    except OSError:
        return ""
    if key == _inst["key"]:
        return _inst["v"]
    m = VER_RE.fullmatch(os.path.basename(real))
    v = m.group(0) if m else ""
    if not v:
        try:
            out = subprocess.run([real, "--version"], capture_output=True, timeout=5)
            m = VER_RE.search(out.stdout.decode("utf-8", "replace"))
            v = m.group(0) if m else ""
        except Exception:
            v = ""
    _inst["key"], _inst["v"] = key, v
    return v


def _older(a, b):
    """Is version a older than version b?"""
    pa, pb = VER_RE.search(a or ""), VER_RE.search(b or "")
    return bool(pa and pb) and tuple(map(int, pa.groups())) < tuple(map(int, pb.groups()))


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


def _ms(stamp):
    """ISO-8601 'Z' timestamp -> epoch ms, or 0."""
    try:
        sec = calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
        frac = stamp[20:23] if stamp[19:20] == "." else ""
        return sec * 1000 + (int(frac.ljust(3, "0")) if frac.isdigit() else 0)
    except Exception:
        return 0


def transcript(sid):
    """(last-message ms, last-activity-snippet) for a session, cached on mtime.

    Not the file's mtime: Claude Code rewrites its trailing metadata (title,
    mode, last prompt) on transcripts that have been idle for days, which would
    make every session look freshly written - and so unread - at once."""
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
        return (c[2], c[1])
    txt, last = "", 0
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
            if not last and o.get("type") in ("user", "assistant"):
                last = _ms(o.get("timestamp") or "")
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
    last = last or int(m * 1000)                 # no message in the tail window
    _tcache[p] = (m, txt, last)
    return (last, txt)


def pane_id(tmux):
    """'new88:@14.%20' -> '%20'."""
    if not tmux or "." not in tmux:
        return ""
    return tmux.rsplit(".", 1)[-1]


ESC_RE = re.compile(r"\x1b\[([0-9;:?]*)([A-Za-z])|\x1b[^\[]")


def _cells(line):
    """[(char, dim)] for one captured line, escape codes read and dropped."""
    out, dim, at = [], False, 0
    for m in ESC_RE.finditer(line):
        out += [(c, dim) for c in line[at:m.start()]]
        at = m.end()
        if m.group(2) != "m":                    # not a colour change
            continue
        ps = [int(p) if p.isdigit() else 0 for p in re.split("[;:]", m.group(1) or "0")]
        i = 0
        while i < len(ps):
            p = ps[i]
            if p in (38, 48, 58):                # 38;5;N and 38;2;R;G;B carry numbers
                i += 3 if ps[i + 1:i + 2] == [5] else 5 if ps[i + 1:i + 2] == [2] else 1
                continue
            if p == 0 or p == 22:
                dim = False
            elif p == 2 or p == 90:              # faint, or the grey of bright-black
                dim = True
            i += 1
    out += [(c, dim) for c in line[at:]]
    return out


def _draft_in(txt):
    """The prompt line of a captured screen, if something you typed is sitting in
    it. Read with its colours: when the box is empty Claude Code fills it with a
    suggested next prompt, drawn faint, and that is not a draft - it is the
    difference between `yes, update the promotion skill too` being yours or its."""
    for ln in reversed(txt.splitlines()[-30:]):
        cells = _cells(ln)
        plain = "".join(c for c, _ in cells)
        s = plain.lstrip()
        for mark in ("❯", ">"):
            if s.startswith(mark):
                start = len(plain) - len(s) + len(mark)
                rest = "".join(c for c, dim in cells[start:] if not dim).strip()
                return rest if rest and not rest.startswith("─") else ""
    return ""


def pane_draft(pane, mux="tmux"):
    """Text sitting in the prompt box, typed but never submitted."""
    if not pane:
        return ""
    if mux == "herdr":
        return _draft_in(herdr.read(pane, ansi=True))
    return _draft_in(_tmux("capture-pane", "-p", "-e", "-t", pane))


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
    rows, latest = [], installed()
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
        said, snip = transcript(d.get("sessionId", ""))
        touched = int(max(d.get("statusUpdatedAt") or 0,
                          d.get("updatedAt") or 0, said))
        rows.append(dict(
            name=d.get("name") or "?",
            sid=d.get("sessionId") or "",
            pid=d.get("pid"),
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
            # Claude Code updates itself on disk, but a running session keeps the
            # version it started with until you restart it ("Restart to update")
            version=d.get("version") or "",
            update=latest if _older(d.get("version"), latest) else "",
        ))
    seen = seen_map()
    # herdr's focused pane says which pane it is, not that anyone is looking: herdr
    # keeps one focused with no terminal attached, and cannot say whether one is.
    # So it is only a marker for the office - it never counts as read.
    watching = herdr.current()[1] if herdr.available() else ""
    for r in rows:
        r["watching"] = bool(watching) and r["sid"] == watching
        r["unread"] = bool(r["touched"]) and r["touched"] > seen.get(r["sid"], 0)
        # herdr keeps its own read mark, and keeps it while nightshift is not
        # running: a pane that finished while you were elsewhere is `done` until
        # you focus it, then `idle`. Our stamp only ever narrows that - opening a
        # session in talk reads it too - otherwise every session you had read in
        # the terminal would come back unread the next time nightshift starts.
        # Not for the focused pane, though: herdr may count that one as looked at
        # and never mark it `done`, so our stamp alone decides it.
        if r["mux"] == "herdr" and not r["watching"]:
            hs = herdr.status_of(r["pane"])
            if hs in ("idle", "working", "blocked", "done"):
                r["unread"] = r["unread"] and hs == "done"
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
PASTE_SETTLE = 0.35         # seconds between typing a message and pressing Enter


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
    # Claude Code reads a burst of input as a paste, and an Enter inside that
    # burst becomes a newline in the prompt - the message sits there as a draft.
    # So the text goes first, and Enter only once it has settled.
    if row["mux"] == "herdr":
        if not (text and submit):
            return herdr.send(pane, text, ["enter"] if submit else [])
        err = herdr.send(pane, text)
        if err:
            return err
        time.sleep(PASTE_SETTLE)
        return herdr.send(pane, "", ["enter"])
    try:
        if text:
            # a buffer + bracketed paste, so a multi-line message stays one prompt
            subprocess.run(["tmux", "set-buffer", "-b", "nightshift", "--", text],
                           capture_output=True, timeout=2, check=True)
            subprocess.run(["tmux", "paste-buffer", "-b", "nightshift", "-d", "-p",
                            "-t", pane], capture_output=True, timeout=2, check=True)
            if submit:
                time.sleep(PASTE_SETTLE)
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


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _pane_exists(pane, mux):
    if mux == "herdr":
        return any(p["id"] == pane for p in herdr.panes(force=True))
    return _tmux("display", "-p", "-t", pane, "#{pane_id}").strip() == pane


def restart(sid, wait=15.0):
    """/exit a session and start it again on the Claude Code now installed,
    carrying on the same conversation - what "Restart to update" asks of you,
    without going to the terminal. '' on success, else why not.

    `claude --resume <id>` rather than `--continue`: continue picks the newest
    conversation in the folder, and several sessions often share one.
    Only an idle session with an empty prompt is restarted: a working one would be
    cut off, one waiting on you has a dialog that /exit would answer, and a draft
    would have /exit typed onto the end of it."""
    if not UUID_RE.match(sid or ""):
        return "bad session id"
    row = next((r for r in collect() if r["sid"] == sid), None)
    if row is None:
        return "that session is not running"
    if row["kind"] != "interactive" or not row["pane"]:
        return "that session has no pane of its own to restart in"
    why = {"busy": "it is working - wait until it is idle",
           "waiting": "it is waiting on you - answer it first",
           "draft": "there is unsent text in its prompt - clear it first"}.get(row["state"])
    if why or row["state"] != "idle":
        return why or "its state is unknown - restart it from the terminal"
    pane, mux, pid = row["pane"], row["mux"], row["pid"]
    cwd = os.path.expanduser(row["cwd"] or "~")
    err = send_pane(pane, "/exit", True)
    if err:
        return err
    end = time.time() + wait
    while alive(pid) and time.time() < end:
        time.sleep(0.25)
    if alive(pid):
        return "it did not exit - it may be asking something, look at its terminal"
    time.sleep(0.6)                              # let the shell draw its prompt
    if not _pane_exists(pane, mux):
        return "the pane closed when it exited - start it again from the terminal"
    # typed at the pane's shell: the id is a checked UUID and the folder is quoted
    cmd = "cd %s && claude --resume %s" % (shlex.quote(cwd), sid)
    if mux == "herdr":
        return herdr.send(pane, cmd, ["enter"])
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, "-l", cmd],
                       capture_output=True, timeout=2, check=True)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"],
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
