# nightshift

A pixel office for every Claude Code session running on this Mac. One room per
tmux session, one desk per agent, rainy night city out the windows. You glance
at it and know who is working and who is waiting on you - and when you want to
know *what* one of them is doing, `nightshift read` shows the conversation.

Three views, same registry:

- **`nightshift`** — the office, in a browser. Click a desk to jump to that
  tmux pane.
- **`nightshift board`** — the same thing as a terminal table, sorted by "how
  much does this one need you": waiting → draft → idle → busy.
- **`nightshift read`** — what they are actually *saying*. Every session on the
  left, its conversation on the right, tailed as it is written. The office
  serves it too, at `/read`, so one command gives you both.

## Run

```
nightshift                 # serve 127.0.0.1:8787 and open a browser
nightshift --port 9000
nightshift --no-open

nightshift board           # live, refresh every 2s
nightshift board --once
nightshift board -n 5
nightshift board --brief   # one line, for tmux status-right

nightshift herdr           # what the herdr backend sees, when it misbehaves

nightshift read            # the reader on its own, 127.0.0.1:8788
nightshift read --port 9001
nightshift read --no-open
```

Nothing has to be switched on to record a session: Claude Code writes every
conversation to disk as it happens. The reader only reads those files.

In the office: `f` toggles fullscreen, hover a desk for its unsent draft,
click a desk to `tmux select-window` + `select-pane` onto it, **shift-click a
desk to read that session's conversation**, `r` (or the header button) opens the
reader on its own.

In the reader: `j`/`k` walk the session list, `/` filters it, `t` hides tool
calls, `h` shows thinking markers, `f` unsticks from the newest message,
`g`/`G` jump to the top or the bottom. Click a tool call to see its full input
and result. **focus pane** puts your terminal on that session.

## Install

Python, editable (what this machine uses):

```
python3 -m pip install -e .
```

Or through npm, which just wraps the same Python entry point:

```
npm link                   # then nightshift on PATH
npx .                      # run without installing
```

## How it works

`docs/architecture.html` is the illustrated version: where the data comes from,
how a session's state is derived, the two clocks, and the canvas-width search.
Open it in a browser.

The short version: Claude Code already writes each session's state to disk, so
nothing here has to be inferred.

The reader adds one more file Claude Code already keeps:
`~/.claude/projects/<slug>/<sessionId>.jsonl`, one JSON object per line,
appended as the conversation happens. So following a live session is a byte
offset: the browser sends back where it stopped and gets only what was written
since. Opening a session reads a window off the end (widened until it holds
enough events - one pasted image can be bigger than the whole window), with
"load full history" for the rest.

Two things that look like bugs but are not: **thinking is never readable** -
Claude Code writes the encrypted signature and an empty string, so the reader
shows only a marker where a pause happened; and **ended sessions have no name**,
because the registry entry is gone the moment the process exits, so they are
listed by their AI title instead.

## tmux, or herdr

The registry records a `tmux` pane reference and nothing else, so a session run
under [herdr](https://herdr.dev) - its own runtime, not a layer on tmux - arrives
with no pane: no room to sit in, no draft detection, no click-to-focus. So when
a session has no tmux pane and herdr's socket is up, `herdr.py` claims one by
walking the session's process ancestry to a pane's shell pid, then speaks to
herdr's socket API in place of tmux:

| | tmux | herdr |
| --- | --- | --- |
| where it sits | registry's `tmux` field | `pane.list` + `pane.process_info` |
| unsent draft | `capture-pane -p` | `pane.read --source visible` |
| click to focus | `select-window` + `select-pane` | `pane.focus` |

Both can be on screen at once - a herdr workspace becomes a room next to the
tmux ones. The socket is spoken to directly rather than through the `herdr` CLI:
it is one newline-terminated JSON object each way, it avoids exec'ing an 19 MB
binary every poll, and `pane.focus` (focus *this* pane) only exists there - the
CLI's `pane focus` moves to a neighbour by direction.

Drafts are read only for `kind: "interactive"` sessions. Background agents are
children of an interactive one and share its pane, so they would otherwise
inherit - and mis-report - its unsent text.

## Stack

Deliberately dependency-free.

| Layer | What |
| --- | --- |
| Data | `~/.claude/sessions/<pid>.json`, written by Claude Code itself, plus `tmux capture-pane` (or herdr's `pane.read`) for unsubmitted drafts, plus the transcript `.jsonl` for the reader |
| Server | `http.server.ThreadingHTTPServer`, stdlib, loopback only; the office also serves the reader at `/read` + `/api/read/*` |
| UI | one HTML file, canvas 2D, hand-rolled pixel renderer, no framework, no build step |

`src/nightshift/office.html` is re-read on every request, so editing it and
refreshing the browser is the whole dev loop. No bundler, no watcher.

## Layout

```
src/nightshift/
  cli.py            entry point: office, or `board` / `read`
  core.py           session discovery, shared by all three views
  office.py         HTTP server + /api/sessions + /api/focus
  fleet.py          terminal renderer
  read.py           transcript parser + the /read routes both servers wear
  herdr.py          herdr backend, for sessions that are not in tmux
  office.html       the animation
  read.html         the reader
bin/                node shim, so npm can install the same entry point
docs/               illustrated architecture notes
```

## Safety note

Both servers bind `127.0.0.1` only. `/api/focus` accepts a tmux pane id matching
`^%\d+$` **and** present in a live `collect()`, then runs only `select-window`
and `select-pane`. It never calls `send-keys` and never builds a shell string.

The reader is read-only and cannot type into a session. `/api/read/transcript`
takes a session id that must match the UUID shape and must resolve through a
glob under `~/.claude/projects/` - no path from the browser ever reaches
`open()`. It is namespaced under `/api/read/` because the office's own
`/api/sessions` must keep returning live sessions only: the reader's list also
carries ended ones, and those have no desk to sit at.
