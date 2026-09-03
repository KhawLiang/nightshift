"""Optional herdr backend, for sessions that are not in tmux.

Claude Code records a `tmux` pane reference in its registry and nothing else, so
under herdr - which is its own runtime, not a layer on tmux - a session arrives
with no pane at all. That costs the three things the registry cannot tell us:
which window a session sits in, whether text is sitting unsent in its prompt,
and how to jump the terminal to it.

herdr's socket API answers all three, so this module claims a pane per session:

    pane.list          the panes, with their workspace and tab
    pane.process_info  each pane's shell pid + foreground process pids
    pane.read          the visible screen, in place of `tmux capture-pane`
    pane.focus         in place of `select-window` + `select-pane`

A session is matched to a pane by walking its process ancestry up to a pane's
shell pid: Claude Code runs as a child of the shell that herdr owns.

The socket is spoken to directly rather than through the `herdr` CLI: it is one
newline-terminated JSON object each way, it avoids exec'ing a 19 MB binary on
every poll, and `pane.focus` (focus *this* pane) exists there while the CLI only
exposes `pane focus --direction`, which moves to a neighbour.

Nothing here runs unless herdr's socket exists and a session actually lacks a
tmux pane. `nightshift herdr` prints what this module can see.
"""
import json, os, re, socket, subprocess, time

ID_RE = re.compile(r"^[A-Za-z0-9_.:@%-]{1,64}$")
TTL = 2.0                       # panes rarely move; collect() runs every 1.5s
_state = {"t": 0.0, "panes": [], "sock_t": 0.0, "sock": ""}


def sock_path():
    """Where the running herdr server is listening, or ''."""
    env = os.environ.get("HERDR_SOCKET")
    if env:
        return env if os.path.exists(env) else ""
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    p = os.path.join(cfg, "herdr", "herdr.sock")
    return p if os.path.exists(p) else ""


def available():
    """Is there a herdr server to talk to? Cached - the poll loop calls this."""
    now = time.time()
    if now - _state["sock_t"] > 5.0:
        _state["sock_t"], _state["sock"] = now, sock_path()
    return bool(_state["sock"])


def call(method, params=None, timeout=2.0):
    """One request, one response. Returns the `result` object, or None."""
    path = _state["sock"] or sock_path()
    if not path:
        return None
    req = json.dumps({"id": "nightshift", "method": method,
                      "params": params or {}}) + "\n"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(req.encode("utf-8"))
        buf = b""
        while b"\n" not in buf and len(buf) < 4_000_000:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.split(b"\n")[0].decode("utf-8", "replace")).get("result")
    except Exception:
        return None


def _labels():
    """{workspace_id: label}, {tab_id: (label, pane_count)} - for the tmux-style
    `workspace:tab` spot, so the office can group a room per workspace."""
    ws, tabs = {}, {}
    r = call("workspace.list") or {}
    for w in r.get("workspaces", []):
        ws[w.get("workspace_id")] = w.get("label") or w.get("workspace_id") or "?"
    r = call("tab.list") or {}
    for t in r.get("tabs", []):
        tabs[t.get("tab_id")] = (t.get("label") or str(t.get("number") or "?"),
                                 t.get("pane_count") or 1)
    return ws, tabs


def panes(force=False):
    """[{id, spot, pids}] for every herdr pane, cached for TTL seconds."""
    now = time.time()
    if not force and now - _state["t"] < TTL:
        return _state["panes"]
    found = []
    if available():
        listed = (call("pane.list") or {}).get("panes", [])
        ws, tabs = _labels() if listed else ({}, {})
        for p in listed:
            pid_ = p.get("pane_id") or ""
            if not ID_RE.match(pid_):
                continue
            tab, npanes = tabs.get(p.get("tab_id"), ("?", 1))
            spot = "%s:%s" % (ws.get(p.get("workspace_id"), "?"), tab)
            if npanes > 1:                   # same rule as tmux: disambiguate
                spot += "." + (pid_.rsplit(":", 1)[-1].lstrip("p") or pid_)
            info = (call("pane.process_info", {"pane_id": pid_}) or {}).get("process_info", {})
            pids = set()
            if isinstance(info.get("shell_pid"), int):
                pids.add(info["shell_pid"])
            for fg in info.get("foreground_processes") or []:
                if isinstance(fg, dict) and isinstance(fg.get("pid"), int):
                    pids.add(fg["pid"])
            found.append({"id": pid_, "spot": spot, "pids": pids})
    _state["t"], _state["panes"] = now, found
    return found


def _ancestors(pid, limit=12):
    """pid and its parents - Claude Code runs under the pane's shell."""
    chain, cur = [], int(pid or 0)
    for _ in range(limit):
        if cur <= 1:
            break
        chain.append(cur)
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(cur)],
                                 capture_output=True, timeout=1.5)
            cur = int(out.stdout.decode().strip() or 0)
        except Exception:
            break
    return chain


def pane_for(pid):
    """(pane id, spot) for the pane a session's process is running in."""
    ps = panes()
    if not ps or not pid:
        return ("", "")
    owned = {q: p for p in ps for q in p["pids"]}
    for anc in _ancestors(pid):
        hit = owned.get(anc)
        if hit:
            return (hit["id"], hit["spot"])
    return ("", "")


def read(pane, lines=30):
    """The pane's visible screen, in place of `tmux capture-pane -p`."""
    if not ID_RE.match(pane or ""):
        return ""
    r = call("pane.read", {"pane_id": pane, "source": "visible", "lines": lines,
                           "format": "text", "strip_ansi": True}) or {}
    return (r.get("read") or {}).get("text", "")


def focus(pane):
    """Jump the terminal to a pane. '' on success, else why not.

    pane.focus only selects - it cannot type, and there is no shell in the path.
    """
    if not ID_RE.match(pane or ""):
        return "not a pane id"
    r = call("pane.focus", {"pane_id": pane})
    if not r:
        return "herdr did not focus %s" % pane
    return ""


def current():
    """(pane_id, sessionId) of the pane you are looking at right now, or ('', '')."""
    r = call("pane.current") or {}
    p = r.get("pane") or {}
    if not p.get("focused"):
        return "", ""
    sess = p.get("agent_session") or {}
    return p.get("pane_id") or "", (sess.get("value") or "")


def send(pane, text="", keys=None):
    """Type into a pane. '' on success, else why not.

    One `pane.send_input` carries the text and the keys together, so a message
    and its Enter land in that order without a race.
    """
    if not ID_RE.match(pane or ""):
        return "not a pane id"
    params = {"pane_id": pane}
    if text:
        params["text"] = text
    if keys:
        params["keys"] = list(keys)
    if len(params) == 1:
        return ""                                   # nothing to send
    r = call("pane.send_input", params)
    if r is None:
        return "herdr did not take input for %s" % pane
    return ""


def main():
    """`nightshift herdr` - what this backend can see, for when it misbehaves."""
    from .core import collect
    path = sock_path()
    print("socket   %s" % (path or "not found (is a herdr server running?)"))
    if not path:
        return
    ping = call("ping") or {}
    print("server   herdr %s, protocol %s" % (ping.get("version", "?"),
                                              ping.get("protocol", "?")))
    ps = panes(force=True)
    print("panes    %d" % len(ps))
    for p in ps:
        print("  %-12s %-24s pids %s" % (p["id"], p["spot"],
                                         ",".join(str(x) for x in sorted(p["pids"]))))
    rows = [r for r in collect()]
    print("sessions %d (%d claimed by herdr)" % (len(rows),
                                                 sum(1 for r in rows if r["mux"] == "herdr")))
    for r in rows:
        print("  %-22s %-6s %-6s %-22s %s" % (r["name"], r["state"], r["mux"] or "-",
                                              r["where"], r["pane"] or "-"))


if __name__ == "__main__":
    main()
