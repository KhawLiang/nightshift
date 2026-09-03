#!/usr/bin/env python3
"""nightshift - pixel-art office view of every Claude Code session on this machine.

Serves a local page (loopback only) that renders each session as a character:
typing at a desk when busy, wandering off when idle, standing up with a `!`
when it's blocked on you.

Usage:  nightshift              start and open a browser
        nightshift --port 9000
        nightshift --no-open
"""
import json, os, sys, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
from .core import collect, focus_pane, interactive
from .talk import TalkRoutes

PAGE = os.path.join(HERE, "office.html")


class Handler(TalkRoutes, BaseHTTPRequestHandler):
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
        path, _, query = self.path.partition("?")
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if self.talk_get(path, q):               # /talk, /read and /api/*
            return
        if path == "/":
            try:
                with open(PAGE, "rb") as f:     # re-read so edits are live
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, "cannot read %s: %s" % (PAGE, e), "text/plain")
        if path == "/api/sessions":              # desks, plus the full roster
            import time
            rows = collect()
            # `sessions` are the desks; `all` also carries the background agents,
            # which have no desk but still show up in the workspace rail.
            return self._json(200, {"now": int(time.time() * 1000),
                                    "sessions": interactive(rows), "all": rows})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 11 * 1024 * 1024)) if n else b""
        if self.talk_post(path, raw, self.headers.get("Content-Type", "")):
            return                               # /api/talk/send and /api/talk/upload
        if path != "/api/focus":
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._json(400, {"error": "bad body"})
        pane = body.get("pane") or ""
        err = focus_pane(pane)          # validates the pane, then selects it
        if err:
            return self._json(400 if "pane" in err else 500, {"error": err})
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
