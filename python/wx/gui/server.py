"""HTTP layer between the window and the agent.

stdlib only. The whole surface is a static directory and two JSON endpoints
plus one SSE stream, and a dependency that exists to route those would be a
dependency PyInstaller has to freeze into a 21 MB binary for no benefit.

SSE rather than WebSocket: the traffic is one-directional (the browser sends
a prompt, the server streams events back), the payload is text, and EventSource
reconnects on its own. A socket would add a framing protocol to solve a
problem that a newline-delimited text format does not already solve.

Bound to 127.0.0.1 on an ephemeral port. The window is the only client, and
the port is discovered at runtime rather than fixed, so two copies of WUXICODE
do not collide -- and nothing on the network can reach the agent.
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

from wx.gui.session import Session

UI_DIR = Path(__file__).resolve().parent / "ui"

#: Refuse to buffer a turn that never ends. The stream is the response body,
#: so a hung provider holds a thread; the cap is what stops that being forever.
SSE_KEEPALIVE_S = 15.0


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
        """Quiet by default.

        A windowed build has no console to print to, so the default handler
        writing to stderr is at best invisible and at worst a crash. WX_DEBUG
        turns it back on for the case where it is wanted.
        """
        import os

        if os.environ.get("WX_DEBUG") == "1":
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A local server that a page could embed is a local server that other
        # pages can drive. The agent runs tools; keep it same-origin only.
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
        # RemoteDisconnected in the browser with nothing useful on the server
        # side. Doing it here means a new dataclass endpoint cannot hit it.
        if is_dataclass(obj) and not isinstance(obj, type):
            obj = asdict(obj)
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            return self._json(self.server.session.state())
        if path == "/api/files":
            return self._json({"files": self.server.session.files()})
        return self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/chat":
            return self._chat()
        if path == "/api/abort":
            self.server.session.abort()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR)
        except ValueError:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")

    # -- streaming --------------------------------------------------------

    def _chat(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        prompt = str(payload.get("prompt") or "")
        if not prompt.strip():
            return self._json({"error": "empty prompt"}, 400)

        session = self.server.session
        if session.busy():
            return self._json({"error": "a turn is already running"}, 409)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        queue: list[dict[str, Any]] = []

        # The session is shared with every other request, so swapping its emit
        # target for the duration of this stream and restoring it afterwards is
        # only safe because concurrent turns are refused above.
        previous_emit = session.emit

        def emit(ev: dict[str, Any]) -> None:
            queue.append(ev)

        session.emit = emit

        try:
            if not session.send(prompt):
                self._write({"type": "error", "message": "a turn is already running"})
                self._write({"type": "end"})
                return

            # Drain until the turn reports itself finished. `finished` is an
            # EVENT and not `not session.busy()`: a worker thread that has been
            # created but has not started running is not yet busy(), so the
            # poll closed the stream immediately and the client saw only the
            # terminator. The worker sets it on its last act, which is the only
            # ordering that cannot race.
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
        # frame early. json.dumps escapes them, but the split is cheap
        # insurance and makes the invariant local rather than assumed.
        for line in data.split("\n"):
            self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
        self.wfile.flush()


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], session: Session) -> None:
        super().__init__(addr, Handler)
        self.session = session


def serve(session: Session, port: int | None = None) -> GuiServer:
    """Bind and return a server already listening. Caller owns shutdown()."""
    port = port or _free_port()
    return GuiServer(("127.0.0.1", port), session)


def main(workspace: str | None = None, port: int | None = None) -> int:
    """Run the server on its own. Used by `python -m wx.gui.server` to develop
    the UI in a normal browser, with a real window available on demand."""
    import os

    ws = Path(workspace or os.getcwd())
    session = Session(workspace=ws)
    srv = serve(session, port)
    host, bound = srv.server_address[:2]
    print(f"WUXICODE GUI  http://{host}:{bound}/")
    print(f"  workspace: {ws}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        session.shutdown()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
