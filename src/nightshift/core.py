"""Shared session-discovery for the terminal board and the pixel office.

Everything comes from the registry Claude Code maintains at ~/.claude/sessions/,
plus a peek at each tmux pane for input typed but never submitted.
"""
import json, os, time, glob, subprocess

HOME = os.path.expanduser("~")
SESS = os.path.join(HOME, ".claude", "sessions")
PROJ = os.path.join(HOME, ".claude", "projects")

RANK = dict(waiting=0, draft=1, idle=2, busy=3, unknown=4)


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


_tcache = {}


def transcript(sid):
    """(mtime, last-activity-snippet) for a session, cached on mtime."""
    if not sid:
        return (0, "")
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
    if not hits:
        return (0, "")
    p = hits[0]
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


def pane_draft(pane):
    """Text sitting in the prompt box, typed but never submitted."""
    if not pane:
        return ""
    txt = _tmux("capture-pane", "-p", "-t", pane)
    for ln in reversed(txt.splitlines()[-30:]):
        s = ln.strip()
        for mark in ("❯", ">"):
            if s.startswith(mark):
                rest = s[len(mark):].strip()
                return rest if rest and not rest.startswith("─") else ""
    return ""


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
        st = (d.get("status") or "unknown").lower()
        if st not in RANK:
            st = "unknown"
        draft = ""
        if st in ("idle", "unknown"):        # only a stopped session holds a draft
            draft = pane_draft(pane_id(tmux))
            if draft:
                st = "draft"
        tm, snip = transcript(d.get("sessionId", ""))
        touched = int(max(d.get("statusUpdatedAt") or 0,
                          d.get("updatedAt") or 0, tm * 1000))
        rows.append(dict(
            name=d.get("name") or "?",
            state=st,
            draft=draft,
            pane=pane_id(tmux),
            where=where(tmux),
            cwd=(d.get("cwd") or "").replace(HOME, "~"),
            started=d.get("startedAt"),
            touched=touched,
            quiet=int(max(0, time.time() - touched / 1000.0)) if touched else 0,
            age=ago(d.get("startedAt")),
            quiet_str=ago(touched),
            snip=snip,
            model=d.get("model") or "",
        ))
    rows.sort(key=lambda r: (RANK[r["state"]], r["name"]))
    return rows
