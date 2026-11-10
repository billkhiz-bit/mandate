"""Local dashboard server: the page in web/ plus a small JSON API.

Standard library only. It listens on 127.0.0.1, refuses requests whose Host
header isn't this machine (a guard against DNS rebinding), and takes POSTs
only as JSON, so a page on another site can't drive it with a plain form.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mandate.brain import BrainUnavailable
from mandate.engine import Engine, summary_of
from mandate.paypal import PayPalError

WEB_DIR = Path(__file__).parent / "web"
MAX_BODY = 64 * 1024


def make_handler(engine: Engine, port: int):
    lock = threading.Lock()
    cache: dict = {"state": summary_of(engine.s, engine.brain.name, engine.paypal.mode, engine.today())}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    brain_name = engine.brain.name

    class Handler(BaseHTTPRequestHandler):
        server_version = "Mandate"

        def log_message(self, fmt, *args):  # quieter console: method, path, status
            pass

        # -- helpers ---------------------------------------------------------
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(403, {"error": "Mandate only answers on this machine."})
            return False

        def _body(self) -> dict:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Send JSON.")
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY:
                raise ValueError("Request too large." if length > 0 else "Bad Content-Length.")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("Send a JSON object.")
            return data

        def _state(self) -> dict:
            """Call with the lock held. Also refreshes the copy GET serves while a run is busy."""
            state = summary_of(engine.s, brain_name, engine.paypal.mode, engine.today())
            cache["state"] = json.loads(json.dumps(state))  # a deep copy, safe to serve later
            return state

        # -- routes ----------------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                # A run holds the lock through slow model calls; don't freeze the page
                # behind it: serve the last snapshot instead, marked busy.
                if lock.acquire(blocking=False):
                    try:
                        return self._json(200, self._state())
                    finally:
                        lock.release()
                return self._json(200, {**cache["state"], "busy": True})
            name = "index.html" if path in ("/", "/index.html") else path.removeprefix("/static/")
            target = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "Not found."})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            if not self._host_ok():
                return
            try:
                body = self._body()
            except (ValueError, UnicodeDecodeError) as exc:
                return self._json(400, {"error": str(exc) or "Bad request."})
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/compile":  # reads the wording only; no lock, so the page stays live
                    text = str(body.get("text", ""))[:4000]
                    if not text.strip():
                        return self._json(400, {"error": "Write the mandate first."})
                    return self._json(200, engine.compile(text))
                with lock:
                    if path == "/api/run":
                        counts = engine.run_inbox()
                        return self._json(200, {"counts": counts, "state": self._state()})
                    if path == "/api/approve":
                        entry = engine.approve(str(body.get("id", "")))
                        return self._json(200, {"entry": entry, "state": self._state()})
                    if path == "/api/decline":
                        entry = engine.decline(str(body.get("id", "")))
                        return self._json(200, {"entry": entry, "state": self._state()})
                    if path == "/api/mandate":
                        engine.use_mandate(body.get("mandate") or {})
                        return self._json(200, {"state": self._state()})
                    if path == "/api/reset":
                        engine.store.reset(engine.today())
                        return self._json(200, {"state": self._state()})
            except KeyError as exc:
                return self._json(404, {"error": str(exc).strip("'\"")})
            except (PayPalError, ValueError) as exc:
                return self._json(409, {"error": str(exc)})
            except BrainUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never leak a traceback to the page
                return self._json(500, {"error": f"Something went wrong ({exc.__class__.__name__})."})
            return self._json(404, {"error": "Not found."})

    return Handler


def serve(engine: Engine, port: int = 8600) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine, port))
    print(f"Mandate is running at http://127.0.0.1:{port}  (AI: {engine.brain.name}; PayPal: {engine.paypal.mode})")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
