# nightshift

A pixel office for every Claude Code session running on this Mac. One room per
tmux session, one desk per agent, rainy night city out the windows. You glance
at it and know who is working and who is waiting on you.

Two views, same data:

- **`nightshift`** — the office, in a browser. Click a desk to jump to that
  tmux pane.
- **`nightshift board`** — the same thing as a terminal table, sorted by "how
  much does this one need you": waiting → draft → idle → busy.

## Run

```
nightshift                 # serve 127.0.0.1:8787 and open a browser
nightshift --port 9000
nightshift --no-open

nightshift board           # live, refresh every 2s
nightshift board --once
nightshift board -n 5
nightshift board --brief   # one line, for tmux status-right
```

In the office: `f` toggles fullscreen, hover a desk for its unsent draft,
click a desk to `tmux select-window` + `select-pane` onto it.

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

## Stack

Deliberately dependency-free.

| Layer | What |
| --- | --- |
| Data | `~/.claude/sessions/<pid>.json`, written by Claude Code itself, plus `tmux capture-pane` for unsubmitted drafts |
| Server | `http.server.ThreadingHTTPServer`, stdlib, loopback only |
| UI | one HTML file, canvas 2D, hand-rolled pixel renderer, no framework, no build step |

`src/nightshift/office.html` is re-read on every request, so editing it and
refreshing the browser is the whole dev loop. No bundler, no watcher.

## Layout

```
src/nightshift/
  cli.py            entry point: office, or `board` for the terminal view
  core.py           session discovery, shared by both views
  office.py         HTTP server + /api/sessions + /api/focus
  fleet.py          terminal renderer
  office.html       the animation
bin/                node shim, so npm can install the same entry point
docs/               illustrated architecture notes
```

## Safety note

The server binds `127.0.0.1` only. `/api/focus` accepts a tmux pane id matching
`^%\d+$` **and** present in a live `collect()`, then runs only `select-window`
and `select-pane`. It never calls `send-keys` and never builds a shell string.
