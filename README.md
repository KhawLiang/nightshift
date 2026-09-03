# nightshift

A pixel office for every Claude Code session running on this Mac. One room per
tmux session, one desk per agent, rainy night city out the windows. You glance
at it and know who is working and who is waiting on you - and when you want to
know *what* one of them is doing, `nightshift talk` shows the conversation -
and lets you answer it.

Two views, same registry:

- **`nightshift`** — the office, in a browser. Click a person to jump to that
  pane, click their desk to read the conversation. A rail down the left side
  lists every running agent grouped by the directory it runs in, background
  agents included - they have no desk, so this is the only place they appear.
- **`nightshift talk`** — what they are actually *saying*, and a box to say
  something back. Every running session on the left grouped by workspace, its
  conversation on the right tailed as it is written, the pane's live screen and
  a compose box at the bottom. The office serves it too, at `/talk`, so one
  command gives you both.

## Run

```
nightshift                 # serve 127.0.0.1:8787 and open a browser
nightshift --port 9000
nightshift --no-open

nightshift talk            # talk on its own, 127.0.0.1:8788
nightshift talk --port 9001
nightshift talk --no-open

nightshift herdr           # what the herdr backend sees, when it misbehaves
```

Nothing has to be switched on to record a session: Claude Code writes every
conversation to disk as it happens. Talk reads those files, and writes back only
through the pane you point it at.

In the office: **click a person** to `tmux select-window` + `select-pane` onto
their pane (hovering lights them up and puts a reticle round them), **click
their desk** to open that conversation in talk. `n` toggles the workspace rail
between full and a strip of dots, `f` toggles fullscreen, `r` (or the header's
`talk →`) opens talk. In the rail: a row opens the conversation, its `▸` focuses
the pane, shift-click does the same.

In talk: `j`/`k` walk the session list, `/` filters it, `t` hides tool calls,
`h` hides thinking, `e` expands every step, `f` unsticks from the newest
message, `g`/`G` jump to the top or the bottom. Everything one turn did between
two replies folds into a single `5 steps · Bash ×3` line; the newest one stays
open, older ones close themselves. The count only ever promises what expanding
will show, so hiding thinking or tools re-counts it.

The compose box sends on enter (shift+enter for a newline), `esc` / `^C` / `↑` /
`↵` press those keys in the pane, and **terminal** above it shows that pane's
real screen, so you can see whether you are typing at a prompt or at a dialog. A
pasted image is saved to `~/.claude/nightshift-paste/` and its path goes into the
message - a terminal cannot carry image bytes, but Claude Code can read a file.

`/clear` does not end a session, it gives it a new id, so talk notices the pane's
old conversation stopped and follows the new one instead of sitting there looking
frozen.

### read and unread

Claude Code has no notion of read/unread, so nightshift keeps its own:
`~/.claude/nightshift-seen.json`, one timestamp per session id. Opening a session
in talk stamps it, so does watching output arrive while following, so does
clicking a person to focus their pane - and so does simply having that pane
focused in herdr, since a pane you are looking at is not one you are behind on
(`pane.current` names the focused pane and the agent session in it; the stamp is
written when the focus moves or every 30s, not on every poll).
Anything written since then makes the session **unread**, and an idle session
with unread output says so on its desk plate, in the rail and in the header
count, in blue instead of green. The session whose pane you have
focused right now says **viewing** instead, in white, with a soft glow and a
faint reticle on its desk, and its row in the rail is highlighted with a white
bar - that one is you. Busy sessions say `working` rather
than the registry's `busy`; a session actively typing is not something you are
behind on.

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

Talk adds one more file Claude Code already keeps:
`~/.claude/projects/<slug>/<sessionId>.jsonl`, one JSON object per line,
appended as the conversation happens. So following a live session is a byte
offset: the browser sends back where it stopped and gets only what was written
since. Opening a session reads a window off the end (widened until it holds
enough events - one pasted image can be bigger than the whole window), with
"load full history" for the rest.

Two things that look like bugs but are not: **thinking is often not readable** -
Claude Code writes an encrypted signature and an empty string, so talk shows a
`encrypted thinking` line where a pause happened; and **ended sessions have no
name**, because the registry entry is gone the moment the process exits. Talk's
sidebar lists only what is still running, grouped by workspace, but the API still
returns the last 40 ended transcripts, so a link to one keeps working.

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

A background agent spawned detached has no pane at all - its process ancestry
never reaches one - so it gets no desk, no `▸` in the rail and nothing to type
into. Claude Code also keeps spares warm: alive, idle and untouched for hours.
Past an hour idle they fold into one `+2 idle bg` line per workspace in the rail,
which opens on click. To end one, its registry filename is its pid:
`kill $(basename ~/.claude/sessions/31048.json .json)`.

For the same reason they get no desk in the office: Claude Code keeps spare ones
warm, they have no prompt of their own, and there is nothing to click through to.
The workspace rail still lists them - a background agent can be doing real work
worth reading. This is what makes the office line up with herdr's own agent
sidebar, which shows one row per pane.

## Stack

Deliberately dependency-free.

| Layer | What |
| --- | --- |
| Data | `~/.claude/sessions/<pid>.json`, written by Claude Code itself, plus `tmux capture-pane` (or herdr's `pane.read`) for unsubmitted drafts, plus the transcript `.jsonl` for talk; the only file nightshift writes is `~/.claude/nightshift-seen.json` (read marks) and pasted images |
| Server | `http.server.ThreadingHTTPServer`, stdlib, loopback only; the office also serves talk at `/talk` + `/api/talk/*` |
| UI | two HTML files, canvas 2D, hand-rolled pixel renderer, no framework, no build step |

`office.html` and `talk.html` are re-read on every request, so editing one and
refreshing the browser is the whole dev loop. No bundler, no watcher. The Python
is loaded once at start, though - change a route and the server needs a restart.

## Endpoints

Everything the two servers answer. `nightshift` serves all of it on one port;
`nightshift talk` serves the `/talk` half and maps `/` to `/talk`.

| | | |
| --- | --- | --- |
| `GET` | `/` | the office (on the talk server, `/talk`) |
| `GET` | `/api/sessions` | `{sessions}` have desks, `{all}` adds background agents |
| `POST` | `/api/focus` | `{pane}` - select that tmux/herdr pane |
| `GET` | `/talk` | the transcript reader and compose box |
| `GET` | `/api/talk/sessions` | live sessions, then the 40 most recent ended ones |
| `GET` | `/api/talk/transcript?sid=&from=` | tail one conversation from a byte offset |
| `GET` | `/api/talk/screen?sid=` | the last 14 lines that pane is showing |
| `POST` | `/api/talk/send` | `{sid, text, submit, key, confirm}` - type into that pane |
| `POST` | `/api/talk/upload` | raw image bytes in, a path on disk out |
| `POST` | `/api/talk/seen` | `{sid}` - you have read this far, clear its unread |

Anything else is a 404.

## Layout

```
src/nightshift/
  cli.py            entry point: the office, or `talk` / `herdr`
  core.py           session discovery, shared by both views
  office.py         HTTP server + /api/sessions + /api/focus
  talk.py           transcript parser + the /talk routes both servers wear
  herdr.py          herdr backend, for sessions that are not in tmux
  office.html       the animation, the workspace rail
  talk.html         the transcript reader and compose box
bin/                node shim, so npm can install the same entry point
docs/               illustrated architecture notes
```

## Safety note

Both servers bind `127.0.0.1` only. `/api/focus` accepts a tmux pane id matching
`^%\d+$` **and** present in a live `collect()`, then runs only `select-window`
and `select-pane`. It never calls `send-keys` and never builds a shell string -
typing has its own route, below.

`/api/talk/transcript` takes a session id that must match the UUID shape and must
resolve through a glob under `~/.claude/projects/` - no path from the browser
ever reaches `open()`. It is namespaced under `/api/talk/` because the office's
own `/api/sessions` must keep returning live sessions only: talk's list also
carries ended ones, and those have no desk to sit at.

`/api/talk/send` is the one route that types: it takes a *session id*, never a
pane, resolves it through the same live `collect()` gate `/api/focus` uses, caps
the text at 8 KB, passes it as a single argv item (herdr `pane.send_input`, or a
tmux buffer + bracketed paste - never a shell string), and only ever presses one
of four keys: `esc`, `ctrl-c`, `up`, `enter`. A session in the `waiting` state
gets the text typed **without** enter, because enter might answer a permission
dialog on screen; committing takes a second, explicit press.
