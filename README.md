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
`talk →`) opens talk. Clicking a workspace name in the rail collapses that
group, remembered across reloads; in compact mode the dots stay, since a strip
of dots is all there is. The rail works like the room does: a row focuses that
pane, its `≡` opens the conversation, and shift-click does the same - a
background agent has no pane of its own, so its row just opens the conversation.

Talk's list leads with each session's title, not its name: Claude Code names a
session after its folder plus two hex digits, so under a folder heading every
name reads the same. The line under it is the herdr tab (or tmux window) it sits
in, those two digits, and how full its context is - amber past 50%, red past 80%.

Claude Code updates itself on disk, but a running session keeps the version it
started with until it is restarted. Each session's registry entry records its
`version`, and `claude` resolves to the newest one installed (the native
installer keeps one file per version and points the command at the latest), so
a session behind it gets a green `update` in the list, the header counts them
(`8 to restart`), and the footer says what Claude Code says:
`✔ Update installed · Restart to update`.

The header's **↻ update** button does the restart from talk: the first click
arms it (`restart it?`), a second within 4s types `/exit` into the pane, waits
for the process to go, then types `cd <its folder> && claude --resume <its id>`
at the shell - the same conversation, same session id, on the new version. It is
`--resume <id>` rather than `--continue` because continue takes the newest
conversation in the folder, and several sessions often share one. It refuses a
session that is working, waiting on you, or has unsent text in its prompt (`/exit`
would land on the end of it), and stops if the process does not exit - a dialog
may be asking something - so the terminal view opens to show what happened.

Paste or drop any file - an image, a PDF, a spreadsheet, up to 50 MB. A browser
never tells a page where a file lives, so talk copies it to
`~/.claude/nightshift-paste/` (its name kept behind a timestamp, spaces made `_`)
and the box shows `[report.pdf]`, or `[20260928-095630.png]` for a screenshot;
the tag becomes the copy's full path when the message is sent.

A message sent while a session is working waits in Claude Code's queue - under
its prompt in the terminal, and not in the conversation until it is picked up.
The transcript records each queue operation (`queue-operation` enqueue / remove /
dequeue), so talk shows the message faded as `queued` the moment it is sent,
settles it as `while it worked` when Claude takes it in mid-turn, and lets it come
back as the next turn when it is dequeued instead.

When Claude asks a question (the AskUserQuestion tool - tabs, checkboxes, "Type
something"), talk shows it above the compose box the way Claude Code draws it:
a tab per question, checkboxes or a single choice, a box for your own answer,
and a review before submitting. The question is already in the transcript; the
answer has to go through the dialog in the pane, which only takes keys, so
`ask.py` works it one key at a time and reads the screen after each step. Number
keys tick (multi-choice) or pick-and-advance (single-choice), Tab moves on,
"Type something" needs the cursor on its row before typing - and arrow keys past
the end of the list leave the dialog for Claude Code's agents view, so none is
ever pressed blind. It presses Submit only once Claude Code's own review screen
lists exactly your answers; anything else stops short and says why, with the
dialog left for you in the terminal.

With a Chinese or Japanese keyboard the `/` key types `、` or `／`; as the first
character in the box talk reads either as `/`, so the command menu still opens.

In talk: `j`/`k` walk the session list, `/` filters it, `t` hides tool calls,
`h` hides thinking, `e` expands every step, `f` unsticks from the newest
message, `g`/`G` jump to the top or the bottom. Everything one turn did between
two replies folds into a single `5 steps · Bash ×3` line; the newest one stays
open, older ones close themselves. The count only ever promises what expanding
will show, so hiding thinking or tools re-counts it.

While a session is working, a line under the newest message says so: bouncing
dots, what it is on (`thinking`, or `running Bash · <its description>` while a
tool call is still waiting on its result) and how long the turn has run. Claude
Code only writes a reply to disk once it is whole, so there is no text to stream -
this is the registry's `busy` plus the newest events. It shows the moment you
send, before the registry catches up.

The compose box sends on enter (shift+enter for a newline), `esc` / `^C` / `↑` /
`↵` press those keys in the pane, and **terminal** above it shows that pane's
real screen, so you can see whether you are typing at a prompt or at a dialog. A
pasted image is saved to `~/.claude/nightshift-paste/` and its path goes into the
message - a terminal cannot carry image bytes, but Claude Code can read a file.

Typing `/` at the start of the box opens a command menu, as Claude Code's own
prompt does: its built-in commands, then the skills and custom commands that
session loaded - read from the `skill_listing` Claude Code writes into the
transcript, so a project's own skills only show for sessions in that project.
`↑`/`↓` choose, `tab` fills the name in and leaves room for arguments, `enter`
completes a half-typed name and runs it once it is typed out in full - so a stray
`/co` never fires `/compact` into a pane - and `esc` closes it. Commands that open
a panel (`/model`, `/config`, `/resume` ...) open the terminal view too, since
that is where the panel appears. The built-ins are a hand-kept list in `talk.py`
(checked against 2.1.283); the transcript never lists them.

### the status line

Talk's footer shows what Claude Code shows under its prompt, for the session
you are reading: the model, a context gauge, and the account's 5h and 7d limits
with the time until each rolls over - the same 5-cell gauges and the same
<50 / 50-80 / >80 colours as `~/.claude/statusline-command.sh`. Hover a gauge for
the token count, the session's cost and its effort level.

Claude Code hands those numbers to the status line command on stdin and nowhere
else, so the script keeps a copy per session, right after `input=$(cat)`:

```sh
ns_sid=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)
if [ -n "$ns_sid" ]; then
    ns_dir="$HOME/.claude/nightshift-status"
    mkdir -p "$ns_dir" && printf '%s' "$input" > "$ns_dir/.$ns_sid.$$" \
        && mv -f "$ns_dir/.$ns_sid.$$" "$ns_dir/$ns_sid.json"
fi
```

A session only writes one when its status line next redraws, which an idle
session does not do. Until then its context is estimated from the newest reply's
token usage in the transcript and reads `~ctx` - the transcript does not record
the window size, so it is taken as 1M when `settings.json` picks a `[1m]` model
or the count is already past 200k. The limits are the account's, so they come
from whichever session's status line ran last. Copies untouched for two weeks
are deleted.

`/clear` does not end a session, it gives it a new id, so talk notices the pane's
old conversation stopped and follows the new one instead of sitting there looking
frozen.

### read and unread

Claude Code has no notion of read/unread, so nightshift keeps its own:
`~/.claude/nightshift-seen.json`, one timestamp per session id. Opening a session
in talk stamps it, so does watching output arrive while following, and so does
clicking a person to focus their pane. Merely being herdr's focused pane does not:
herdr keeps a pane focused with no terminal attached - run it in the background
and nobody is looking - and it has no way to say whether a terminal is.
A message written since then makes the session **unread** - the timestamp of
the last user or assistant message, not the file's mtime, because Claude Code
rewrites the trailing metadata of transcripts that have sat idle for days.

herdr keeps a read mark of its own, and keeps it while nightshift is not
running: a pane that finished while you were elsewhere is `done` until you focus
it, then `idle`. On a herdr pane that decides, and our stamp only narrows it
(opening the session in talk reads it too). Without that, every session you had
read in the terminal came back unread each time nightshift started, since it
could not have seen you look. tmux has no such mark, so tmux panes use the stamp
alone, and so does herdr's focused pane, which herdr may count as looked at.

An idle session with unread output says so on its desk plate, in the rail and in the header
count, in blue instead of green. In the office, the session whose pane herdr
has focused says **viewing**, in white, with a soft glow and a faint reticle on
its desk; talk does not show it, since with herdr in the background it says
nothing about where you are. Busy sessions say `working` rather
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
| unsent draft | `capture-pane -p -e` | `pane.read --source visible --format ansi` |
| click to focus | `select-window` + `select-pane` | `pane.focus` |

Both can be on screen at once - a herdr workspace becomes a room next to the
tmux ones. The socket is spoken to directly rather than through the `herdr` CLI:
it is one newline-terminated JSON object each way, it avoids exec'ing an 19 MB
binary every poll, and `pane.focus` (focus *this* pane) only exists there - the
CLI's `pane focus` moves to a neighbour by direction.

Drafts are read with their colours: when the prompt is empty Claude Code fills it
with a suggested next prompt, drawn faint, and only text that is not faint is
something you typed.

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
| Data | `~/.claude/sessions/<pid>.json`, written by Claude Code itself, plus `tmux capture-pane` (or herdr's `pane.read`) for unsubmitted drafts, plus the transcript `.jsonl` for talk; the only file nightshift writes is `~/.claude/nightshift-seen.json` (read marks) and pasted images; the status line script writes `~/.claude/nightshift-status/` |
| Server | `http.server.ThreadingHTTPServer`, stdlib, loopback only; the office also serves talk at `/talk` + `/api/talk/*` |
| UI | two HTML files, canvas 2D, hand-rolled pixel renderer, no framework, no build step; talk is set in JetBrains Mono, bundled under `fonts/` (OFL) so it needs no network |

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
| `GET` | `/api/talk/sessions` | live sessions (each with its `usage`), then the 40 most recent ended ones, plus the account `limits` |
| `GET` | `/api/talk/transcript?sid=&from=` | tail one conversation from a byte offset |
| `GET` | `/api/talk/commands?sid=` | what `/` completes to in that session |
| `GET` | `/api/talk/screen?sid=` | the last 14 lines that pane is showing |
| `POST` | `/api/talk/send` | `{sid, text, submit, key, confirm}` - type into that pane |
| `POST` | `/api/talk/upload` | raw file bytes in (name in `X-Filename`), a path on disk out |
| `GET` | `/fonts/jetbrains-mono-*.woff2` | the bundled font, and nothing else from that folder |
| `POST` | `/api/talk/seen` | `{sid}` - you have read this far, clear its unread |
| `POST` | `/api/talk/answer` | `{sid, id, picks}` - answer the question Claude is asking, through its dialog |
| `POST` | `/api/talk/restart` | `{sid}` - `/exit`, then `claude --resume <sid>` in the same pane |

Anything else is a 404.

## Layout

```
src/nightshift/
  cli.py            entry point: the office, or `talk` / `herdr`
  core.py           session discovery, shared by both views
  office.py         HTTP server + /api/sessions + /api/focus
  talk.py           transcript parser + the /talk routes both servers wear
  ask.py            answers a Claude question by working its dialog in the pane
  herdr.py          herdr backend, for sessions that are not in tmux
  office.html       the animation, the workspace rail
  talk.html         the transcript reader and compose box
  fonts/            JetBrains Mono (latin, latin-ext, vietnamese; OFL.txt)
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
