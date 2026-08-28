#!/usr/bin/env python3
"""nightshift - pixel-art office view of every Claude Code session on this machine.

Serves a local page (loopback only) that renders each session as a character:
typing at a desk when busy, wandering off when idle, standing up with a `!`
when it's blocked on you.

Usage:  nightshift              start and open a browser
        nightshift --port 9000
        nightshift --no-open
"""
import json, os, re, sys, subprocess, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
from .core import collect

PAGE = os.path.join(HERE, "office.html")
PANE_RE = re.compile(r"^%\d+$")


class Handler(BaseHTTPRequestHandler):
    server_version = "nightshift"

    def log_message(self, *a):
        pass                                   # keep the terminal quiet

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            try:
                with open(PAGE, "rb") as f:     # re-read so edits are live
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, "cannot read %s: %s" % (PAGE, e), "text/plain")
        if path == "/api/sessions":
            import time
            return self._json(200, {"now": int(time.time() * 1000),
                                    "sessions": collect()})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/focus":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad body"})
        pane = body.get("pane") or ""
        # 1. shape must be a tmux pane id and nothing else
        if not isinstance(pane, str) or not PANE_RE.match(pane):
            return self._json(400, {"error": "not a pane id"})
        # 2. and it must be a pane we are actually tracking right now
        if pane not in {s["pane"] for s in collect() if s["pane"]}:
            return self._json(400, {"error": "unknown pane"})
        # 3. select-window/select-pane only - never send-keys, never a shell string
        try:
            for cmd in ("select-window", "select-pane"):
                subprocess.run(["tmux", cmd, "-t", pane],
                               capture_output=True, timeout=2, check=True)
        except Exception as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, {"ok": True, "pane": pane})


def main():
    args = sys.argv[1:]
    port = 8787
    if "--port" in args:
        try: port = int(args[args.index("--port") + 1])
        except Exception: pass
    url = "http://localhost:%d" % port
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print("nightshift: cannot bind %s (%s)" % (url, e)); sys.exit(1)
    print("nightshift serving %s  (ctrl-c to stop)" % url)
    if "--no-open" not in args:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nnightshift stopped")



if __name__ == "__main__":
    main()
