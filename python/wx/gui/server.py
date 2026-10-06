"""HTTP layer between the window and the agents.

stdlib only. The whole surface is a static directory, a handful of JSON
endpoints and one SSE stream per turn, and a dependency that exists to route
those would be a dependency PyInstaller has to freeze into a 28MB binary for no
benefit.

SSE rather than WebSocket: the traffic is one-directional (the browser sends a
prompt, the server streams events back), the payload is text, and a
newline-delimited frame is something HTTP/1.1 already transports. A socket
would add a framing protocol to solve a problem that does not exist here.

Every request names the tab it belongs to, because each tab has its own agent
-- see gui/pool.py for why sharing one was not an option.

Bound to 127.0.0.1 on an ephemeral port. The window is the only client, and the
port is discovered at runtime rather than fixed, so two copies of WUXICODE do
not collide -- and nothing on the network can reach an agent that runs shell
tools.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import threading
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wx.gui import pool as poolmod
from wx.gui.pool import SessionPool

UI_DIR = Path(__file__).resolve().parent / "ui"

#: Longest prompt accepted. A prompt is a request, not an upload, and a body
#: this size means something is looping.
MAX_BODY = 1 << 20


def _free_port() -> int:
    """An unused localhost port.

    Bound then released rather than passing port=0 to the server, so the port
    is known before the window is created and can be logged if the GUI fails to
    come up.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Handler(BaseHTTPRequestHandler):
    server_version = "WUXICODE"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Quiet unless WX_DEBUG.

        A windowed build has no console to print to, so the default handler
        writing to stderr is at best invisible and at worst a crash on a closed
        handle.
        """
        import os

        if os.environ.get("WX_DEBUG") == "1":
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A local server that a page could embed is a local server that other
        # pages can drive. The agent runs shell tools; keep it same-origin.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The window closed mid-response. Normal, and not worth a traceback.
            pass

    def _json(self, obj: Any, code: int = 200) -> None:
        # asdict is applied defensively rather than at the call sites: the
        # endpoints return dataclasses, and forgetting to unwrap one produced a
        # RemoteDisconnected in the browser with nothing useful server-side.
        if is_dataclass(obj) and not isinstance(obj, type):
            obj = asdict(obj)
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _query(self) -> tuple[str, dict[str, list[str]]]:
        p = urlparse(self.path)
        return p.path, parse_qs(p.query)

    def _tab(self, q: dict[str, list[str]]) -> int:
        """Which tab this request is for.

        Defaults to 1. A missing tab is a client bug, not a reason to fail the
        request: answering tab 1 is better than answering nothing.
        """
        raw = (q.get("tab") or ["1"])[0]
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 1

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY:
            return {}
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        path, q = self._query()
        p = self.server.pool

        if path == "/api/state":
            return self._json(p.get(self._tab(q)).state())
        if path == "/api/files":
            return self._json({"files": p.get(self._tab(q)).files()})
        if path == "/api/file":
            return self._json(poolmod.read_file(p.workspace, (q.get("path") or [""])[0]))
        if path == "/api/git/status":
            return self._json(poolmod.git_status(p.workspace))
        if path == "/api/git/diff":
            return self._json(poolmod.git_diff(
                p.workspace,
                (q.get("path") or [None])[0],
                staged=(q.get("staged") or ["0"])[0] in ("1", "true", "True"),
            ))
        if path == "/api/git/log":
            return self._json(poolmod.git_log(p.workspace))
        return self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path, q = self._query()
        # Read the body ONCE, here, for every POST. A route that ignores it
        # leaves those bytes in the socket, and on a keep-alive connection the
        # next request is then parsed starting at the tail of the previous body
        # -- which surfaces to the caller as an aborted connection at random,
        # minutes later, on an unrelated request. Routes read it from this
        # value instead of calling _body() again.
        payload = self._body()
        if path == "/api/chat":
            return self._chat(q, payload)
        if path == "/api/abort":
            # Abort stops the TURN and keeps the session: stopping a runaway
            # tool run must not cost the tab its conversation history. Closing
            # the tab is the other endpoint's job.
            session = self.server.pool.peek(self._tab(q))
            was_busy = bool(session and session.busy())
            if session is not None:
                session.abort()
            return self._json({"ok": True, "aborted": was_busy})
        if path == "/api/tab/close":
            return self._json({"ok": True, "closed": self.server.pool.close(self._tab(q))})
        return self._json({"error": "not found"}, 404)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        try:
            target = (UI_DIR / rel).resolve()
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")

    # -- streaming --------------------------------------------------------

    def _chat(self, q: dict[str, list[str]], payload: dict[str, Any]) -> None:
        prompt = str(payload.get("prompt") or "")
        if not prompt.strip():
            return self._json({"error": "empty prompt"}, 400)

        tab = self._tab(q)
        session = self.server.pool.get(tab)
        if session.busy():
            return self._json({"error": "a turn is already running on this tab"}, 409)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        queue: list[dict[str, Any]] = []

        def emit(ev: dict[str, Any]) -> None:
            queue.append(ev)

        previous_emit = session.emit
        session.emit = emit

        try:
            if not session.send(prompt):
                self._write({"type": "error", "message": "a turn is already running"})
                self._write({"type": "end"})
                return

            # Drain until the turn reports itself finished. `finished` is an
            # EVENT and not `not session.busy()`: a worker thread that has been
            # created but has not started running is not yet busy(), so the poll
            # closed the stream immediately and the client saw only the
            # terminator.
            finished = threading.Event()
            inner = emit

            def emit_and_finish(ev: dict[str, Any]) -> None:
                inner(ev)
                if ev.get("type") in ("done", "error", "interrupted", "aborted"):
                    finished.set()

            session.emit = emit_and_finish

            while not finished.wait(timeout=0.15):
                while queue:
                    self._write(queue.pop(0))
            while queue:
                self._write(queue.pop(0))
            self._write({"type": "end"})
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Window closed. Stop the turn rather than let it run to completion
            # into a stream nobody is reading.
            session.abort()
        finally:
            session.emit = previous_emit
            self.close_connection = True

    def _write(self, ev: dict[str, Any]) -> None:
        data = json.dumps(ev, ensure_ascii=False)
        # A payload containing a newline would otherwise terminate the SSE
        # frame early. json.dumps escapes them, but the split is cheap insurance
        # and makes the invariant local rather than assumed.
        for line in data.split("\n"):
            self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
        self.wfile.flush()


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], pool: SessionPool) -> None:
        super().__init__(addr, Handler)
        self.pool = pool


def serve(pool: SessionPool, port: int | None = None) -> GuiServer:
    """Bind and return a server already listening. Caller owns shutdown()."""
    port = port or _free_port()
    return GuiServer(("127.0.0.1", port), pool)


def main(workspace: str | None = None, port: int | None = None) -> int:
    """Run the server on its own. Used by `python -m wx.gui.server` to develop
    the UI in a normal browser, with a real window available on demand."""
    import os

    ws = Path(workspace or os.getcwd())
    p = SessionPool(workspace=ws)
    srv = serve(p, port)
    host, bound = srv.server_address[:2]
    print(f"WUXICODE GUI  http://{host}:{bound}/")
    print(f"  workspace: {ws}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        p.shutdown()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
