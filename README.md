# cc-office

Two status boards for every Claude Code session running on this Mac.

- **`cc-office`** — pixel-art cutaway office in the browser. One room per tmux
  session, one desk per agent, rainy-night skyline out the windows. Click a
  desk to jump to that tmux pane.
- **`cc-fleet`** — the same data as a terminal table, sorted by "how much does
  this one need you": waiting → draft → idle → busy.

## Run

```
cc-office                 # serve 127.0.0.1:8787 and open a browser
cc-office --port 9000
cc-office --no-open

cc-fleet                  # live, refresh every 2s
cc-fleet --once
cc-fleet -n 5
cc-fleet --brief          # one line, for tmux status-right
```

In the office page: `f` toggles fullscreen, hover a desk for its draft text,
click a desk to `tmux select-window` + `select-pane` onto it.

## Install

Python, editable (what this machine uses):

```
python3 -m pip install -e .
```

Or through npm, which just wraps the same Python entry points:

```
npm link                  # then cc-office / cc-fleet on PATH
npx .                     # run without installing
```

## How it works

`docs/architecture.html` is the illustrated version: where the data comes
from, how a session's state is derived, the two clocks, and the canvas-width
search. Open it in a browser.

## Stack

Deliberately dependency-free.

| Layer | What |
| --- | --- |
| Data | `~/.claude/sessions/<pid>.json`, written by Claude Code itself, plus `tmux capture-pane` for unsubmitted drafts |
| Server | `http.server.ThreadingHTTPServer`, stdlib, loopback only |
| UI | one HTML file, canvas 2D, hand-rolled pixel renderer, no framework, no build step |

`src/ccoffice/cc-office.html` is re-read on every request, so editing it and
refreshing the browser is the whole dev loop. No bundler, no watcher.

## Layout

```
src/ccoffice/
  core.py           session discovery, shared by both frontends
  office.py         HTTP server + /api/sessions + /api/focus
  fleet.py          terminal renderer
  cc-office.html    the animation
bin/                node shims, so npm can install the same thing
```

## Safety note

The server binds `127.0.0.1` only. `/api/focus` accepts a tmux pane id matching
`^%\d+$` **and** present in a live `collect()`, then runs only `select-window`
and `select-pane`. It never calls `send-keys` and never builds a shell string.
