"""Answer a Claude Code question (the AskUserQuestion tool) from talk.

The question itself is in the transcript: an AskUserQuestion tool call with every
tab, option and description, and no result yet while it waits. Answering it means
driving the dialog Claude Code draws in the pane, and that dialog only takes keys.
What each key does, measured against 2.1.283:

    multi-choice tab    1-4 ticks or unticks that option; the cursor stays put
    single-choice tab   1-4 picks it and moves on to the next tab
    "Type something"    single: its number puts the cursor in it; type, then Enter
                        multi: its number only ticks it - the cursor has to be moved
                        onto the row (Up/Down) before typing, and a digit typed while
                        it is there lands in the text
    Tab                 next tab; after the last question, the review screen
    Left                previous tab
    review screen       1 submits, 2 cancels

Arrow keys past the end of the list leave the dialog altogether (Claude Code opens
its background-agents view), so nothing here presses a key blind: the screen is
read after every step, and Submit is pressed only once the review screen lists
exactly the answers that were asked for. Anything unexpected stops before that,
with the dialog left for you to finish in the terminal.
"""
import json, os, re, subprocess, time

from . import herdr
from .core import collect, transcript_path, UUID_RE

STEP = 0.3                                   # the dialog redraws within ~100ms
TAB_RE = re.compile(r"^\s*←\s.*\bSubmit\s*→\s*$")
OPT_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*?)\s*$")
BOX_RE = re.compile(r"^\[(.)\]\s*(.*)$")
PLACEHOLDER = re.compile(r"^Type something\.?$")


def pending(sid):
    """(tool id, questions) of the AskUserQuestion still waiting for an answer."""
    path = transcript_path(sid)
    if not path:
        return "", []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return "", []
    asked, answered = {}, set()
    order = []
    for ln in lines:
        if "AskUserQuestion" not in ln and "tool_result" not in ln:
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        cont = (o.get("message") or {}).get("content")
        for b in cont if isinstance(cont, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                asked[b.get("id")] = (b.get("input") or {}).get("questions") or []
                order.append(b.get("id"))
            elif b.get("type") == "tool_result":
                answered.add(b.get("tool_use_id"))
    for tid in reversed(order):
        if tid not in answered:
            return tid, asked[tid]
        break                                    # the newest one is answered
    return "", []


def _norm(s):
    return re.sub(r"\s+", "", (s or "").replace("\xa0", " "))


def parse(screen):
    """What the dialog shows: the question, each numbered row, or the review."""
    lines = [ln.replace("\xa0", " ").rstrip() for ln in (screen or "").splitlines()]
    tab = next((i for i in range(len(lines) - 1, -1, -1) if TAB_RE.match(lines[i])), None)
    if tab is None:
        return None
    body = [ln for ln in lines[tab + 1:] if ln.strip() and not set(ln.strip()) <= set("─━│| ")]
    if body and "Review your answers" in body[0]:
        answers, cur = [], None
        for ln in body[1:]:
            t = ln.strip()
            if t.startswith("Ready to submit"):
                break
            if t.startswith("●"):
                cur = {"q": t[1:].strip(), "a": ""}
                answers.append(cur)
            elif t.startswith("→") and cur is not None:
                cur["a"] = t[1:].strip()
                cur["in_a"] = True
            elif cur is not None:                # a wrapped line of either part
                cur["a" if cur.get("in_a") else "q"] += " " + t
        return {"review": True, "answers": answers}
    question = body[0].lstrip("│| ").strip() if body else ""
    rows, cursor = {}, None
    for ln in body[1:]:
        m = OPT_RE.match(ln)
        if not m:
            continue
        num, text = int(m.group(2)), m.group(3)
        box = BOX_RE.match(text)
        rows[num] = {"checked": (box.group(1) != " ") if box else text.endswith(" ✔"),
                     "label": (box.group(2) if box else re.sub(r"\s*✔$", "", text)).strip()}
        if m.group(1):
            cursor = num
    return {"review": False, "question": question, "rows": rows, "cursor": cursor}


class Driver:
    """Keys into one pane, and its screen back."""

    TMUX = {"tab": "Tab", "left": "Left", "up": "Up", "down": "Down",
            "backspace": "BSpace", "enter": "Enter"}

    def __init__(self, row):
        self.pane, self.mux = row["pane"], row["mux"]

    def screen(self):
        if self.mux == "herdr":
            return herdr.read(self.pane, 120)
        out = subprocess.run(["tmux", "capture-pane", "-p", "-t", self.pane],
                             capture_output=True, timeout=2)
        return out.stdout.decode("utf-8", "replace")

    def key(self, name, times=1):
        for _ in range(times):
            if self.mux == "herdr":
                herdr.send(self.pane, "", [name])
            else:
                subprocess.run(["tmux", "send-keys", "-t", self.pane, self.TMUX[name]],
                               capture_output=True, timeout=2)
            time.sleep(STEP)

    def text(self, s):
        if self.mux == "herdr":
            herdr.send(self.pane, s, [])
        else:
            subprocess.run(["tmux", "send-keys", "-t", self.pane, "-l", s],
                           capture_output=True, timeout=2)
        time.sleep(STEP + 0.1)

    def now(self):
        return parse(self.screen())


def _on(state, q):
    """Is the dialog showing this question?"""
    return bool(state) and not state["review"] and bool(state["question"]) and \
        _norm(q.get("question", "")).startswith(_norm(state["question"])[:60])


def expected(q, pick):
    labels = [o.get("label", "") for o in q.get("options") or []]
    got = [labels[i] for i in sorted(pick["opts"])]
    if pick["other"]:
        got.append(pick["other"])
    return ", ".join(got)


def _cursor_to(d, want):
    """Up/Down onto numbered row `want`, one step at a time, never past the ends."""
    for _ in range(12):
        s = d.now()
        if not s or s["review"] or s["cursor"] is None:
            return False
        if s["cursor"] == want:
            return True
        d.key("down" if s["cursor"] < want else "up")
    return False


def answer(sid, tool_id, picks):
    """Answer the open question in that session's pane. '' on success, else why
    not - and when it stops, it stops before Submit."""
    if not UUID_RE.match(sid or ""):
        return "bad session id"
    row = next((r for r in collect() if r["sid"] == sid), None)
    if row is None or not row["pane"] or row["kind"] != "interactive":
        return "that session is not running in a pane talk can type into"
    tid, qs = pending(sid)
    if not tid or tid != tool_id:
        return "that question is no longer open"
    if not isinstance(picks, list) or len(picks) != len(qs):
        return "one answer per question"
    clean = []
    for q, p in zip(qs, picks):
        n = len(q.get("options") or [])
        opts = sorted({int(i) for i in (p or {}).get("opts") or []
                       if isinstance(i, int) and 0 <= i < n})
        other = " ".join(str((p or {}).get("other") or "").split())[:500]
        if not q.get("multiSelect") and len(opts) + bool(other) != 1:
            return "pick one answer for \"%s\"" % q.get("header", "")
        clean.append({"opts": opts, "other": other})

    d = Driver(row)
    s = d.now()
    if not s:
        return "the question is not on its screen - look at the terminal"
    for _ in range(len(qs) + 1):                 # back to the first tab
        if _on(s, qs[0]):
            break
        d.key("left")
        s = d.now()
    else:
        return "could not find the first question on screen"

    for i, (q, p) in enumerate(zip(qs, clean)):
        s = d.now()
        if not _on(s, q):
            return "lost track at question %d - nothing was submitted" % (i + 1)
        n = len(q.get("options") or [])
        other_row = n + 1
        if q.get("multiSelect"):
            if s["cursor"] == other_row:         # a digit would land in the text
                d.key("up")
                s = d.now()
            for k in range(1, n + 1):
                if s["rows"].get(k, {}).get("checked") != ((k - 1) in p["opts"]):
                    d.text(str(k))
            s = d.now()
            orow = s["rows"].get(other_row, {})
            typed = "" if PLACEHOLDER.match(orow.get("label", "")) else orow.get("label", "")
            if p["other"] and typed != p["other"]:
                if not _cursor_to(d, other_row):
                    return "could not reach \"Type something\" - nothing was submitted"
                d.key("backspace", len(typed))
                d.text(p["other"])
                d.key("up")                      # out of the text, before any digit
            elif not p["other"] and orow.get("checked"):
                d.text(str(other_row))
            s = d.now()
            ok = _on(s, q) and all(s["rows"].get(k, {}).get("checked") == ((k - 1) in p["opts"])
                                   for k in range(1, n + 1))
            if not ok:
                return "the ticks on screen did not come out right - nothing was submitted"
            d.key("tab")
        elif p["opts"]:
            d.text(str(p["opts"][0] + 1))        # picks it and moves on
        else:
            d.text(str(other_row))               # into the text box
            s = d.now()
            orow = s["rows"].get(other_row, {}) if s else {}
            if not s or s.get("cursor") != other_row:
                return "could not reach \"Type something\" - nothing was submitted"
            typed = "" if PLACEHOLDER.match(orow.get("label", "")) else orow.get("label", "")
            d.key("backspace", len(typed))
            d.text(p["other"])
            d.key("enter")

    time.sleep(STEP)
    s = d.now()
    if not s or not s["review"]:
        return "did not reach the review screen - nothing was submitted"
    def shown(q):                                # its line on the review, by prefix
        key = _norm(q.get("question", ""))[:30]
        return next((_norm(a["a"]) for a in s["answers"] if _norm(a["q"])[:30] == key), None)
    for q, p in zip(qs, clean):
        want = expected(q, p)
        if not want:
            continue                             # a multi-choice left empty
        if shown(q) != _norm(want):
            return ("the review screen does not show \"%s\" for \"%s\" - nothing was "
                    "submitted, finish it in the terminal" % (want, q.get("header", "")))
    d.text("1")                                  # Submit answers
    return ""
