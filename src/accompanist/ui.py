"""The stage screen: a page in your browser (or on a phone or tablet on the music stand) that
shows what the band is doing and has buttons for what the keys do.

A small web server in its own thread (Python's standard library only: it must run on a
Raspberry Pi as well as a Mac). It never touches the band itself: the run loop hands it a
snapshot of the Controller's state (publish), and takes the page's requests from it (take)
and carries them out through the Controller, like a key press. So a page can't do anything
a key, a pedal or a CC can't.

  GET  /            the page (web/stage.html)
  GET  /state       the latest snapshot, as JSON
  POST /do          {"action": "break"}            an action (config.ACTIONS)
  POST /set         {"key": "pad.feel", "value": 0.6}   a live parameter

Every request carries the page's key, so only someone given the page's address can use
it. The key and port stay the same from run to run (the key is kept in
~/.accompanist/ui-key), so a page left open simply reconnects when the band starts again,
and no new one is opened while one is already watching.
"""
from __future__ import annotations

import json
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

PAGE = Path(__file__).parent / "web" / "stage.html"
PORT_TRIES = 10            # the port taken: try the next ones
MAX_BODY = 4096            # requests are tiny
KEY_FILE = Path("~/.accompanist/ui-key").expanduser()
WATCHING_S = 2.5           # a page left open reconnects within this long (it asks 5 times a second)


def stage_key() -> str:
    """The page's key, the same each run (so open pages reconnect); made once."""
    try:
        key = KEY_FILE.read_text().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(9)
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key)
        KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return key


class StageServer:
    def __init__(self, port: int, lan: bool = False) -> None:
        self.key = stage_key()
        self.last_seen = float("-inf")         # when a page last asked (time.monotonic())
        self.requests: "queue.Queue[tuple[str, Any, Any]]" = queue.Queue()
        self._snapshot = b"{}"
        self._lock = threading.Lock()
        handler = _handler(self)
        host = "0.0.0.0" if lan else "127.0.0.1"
        last: Optional[OSError] = None
        for p in range(port, port + PORT_TRIES):
            try:
                self.httpd = ThreadingHTTPServer((host, p), handler)
                break
            except OSError as e:
                last = e
        else:
            raise OSError(f"no free port from {port} to {port + PORT_TRIES - 1} ({last})")
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.lan = lan
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/?k={self.key}"

    @property
    def lan_url(self) -> Optional[str]:
        ip = lan_address() if self.lan else None
        return f"http://{ip}:{self.port}/?k={self.key}" if ip else None

    def publish(self, state: dict) -> None:
        """The run loop's latest view of the band (any JSON-able dict)."""
        data = json.dumps(state, default=str).encode()
        with self._lock:
            self._snapshot = data

    def take(self) -> list[tuple[str, Any, Any]]:
        """The page's requests since the last call: ("do", action, None) or ("set", key, value)."""
        out = []
        while True:
            try:
                out.append(self.requests.get_nowait())
            except queue.Empty:
                return out

    def open_page_unless_watched(self) -> None:
        """Open the page in the browser, unless one left open reconnects first."""
        def later() -> None:
            time.sleep(WATCHING_S)
            if time.monotonic() - self.last_seen > WATCHING_S:
                import webbrowser
                try:
                    webbrowser.open(self.url)
                except Exception:
                    pass

        threading.Thread(target=later, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def snapshot(self) -> bytes:
        with self._lock:
            return self._snapshot


def lan_address() -> Optional[str]:
    """This machine's address on the local network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _handler(server: StageServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:       # the terminal is for the band
            pass

        def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self) -> bool:
            from urllib.parse import parse_qs, urlparse

            given = self.headers.get("X-Key") or parse_qs(urlparse(self.path).query).get("k", [""])[0]
            return secrets.compare_digest(given, server.key)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path == "/state" and self._allowed():
                server.last_seen = time.monotonic()
            if not self._allowed():
                self._send(403, b"This page needs the address the accompanist printed "
                                b"(it ends in ?k=...).", "text/plain; charset=utf-8")
            elif path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/state":
                self._send(200, server.snapshot())
            else:
                self._send(404, b"{}")

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if not self._allowed():
                self._send(403, b'{"error": "wrong key"}')
                return
            try:
                n = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, b'{"error": "bad request"}')
                return
            if path == "/do" and isinstance(body.get("action"), str):
                server.requests.put(("do", body["action"], None))
            elif path == "/set" and isinstance(body.get("key"), str) and "value" in body:
                server.requests.put(("set", body["key"], body["value"]))
            else:
                self._send(400, b'{"error": "bad request"}')
                return
            self._send(200, b'{"ok": true}')

    return Handler


def stage_state(ctl, now: float, set_info: Optional[dict], messages: list[str]) -> dict:
    """What the page shows: the Controller's state, the song and set, the voices' on/off and
    feel (the registry's primary parameters), and the latest messages."""
    from . import params as registry

    s = ctl.get_state(now)
    cfg = ctl.cfg
    voices = []
    for p in registry.PARAMS:
        if not p.primary or not p.live:
            continue
        section, name = p.key.split(".", 1)
        voices.append({"key": p.key, "label": p.label, "help": p.help, "type": p.type.__name__,
                       "min": p.min, "max": p.max, "step": p.step,
                       "value": getattr(getattr(cfg, section), name), "group": p.group})
    s["voices"] = voices
    from .reckoner import FINISH_SHAPES, START_SHAPES, describe_finish, describe_start
    s["start"] = {"key": "start.shape", "shape": cfg.start.shape, "text": describe_start(cfg),
                  "choices": list(START_SHAPES)}
    s["finish"] = {"key": "ending.shape", "shape": cfg.ending.shape, "text": describe_finish(cfg),
                   "choices": list(FINISH_SHAPES)}
    s["title"] = cfg.song.title
    s["set"] = set_info
    s["messages"] = messages[-4:]
    return s
