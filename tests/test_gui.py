"""The GUI's HTTP surface, exercised for real.

A window that fails to open is a much worse bug than a window that fails a
test, and most of what can go wrong here (a route that 404s, a static path
that escapes the UI directory, a stream that never terminates) is invisible
until something loads it. So this starts a real server on a real port and
talks to it over HTTP.

The agent is not stubbed at the HTTP layer; only the Session's turn execution
is, because a test that calls a live model is a test that costs money and
fails when the network does.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from wx.gui import server as srv  # noqa: E402
from wx.gui.session import Session  # noqa: E402


@pytest.fixture()
def live(tmp_path: Path):
    """A running server with a scripted session."""
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")

    emitted: list[dict] = []

    session = Session.__new__(Session)  # do not build a real agent
    session.workspace = tmp_path
    session.emit = emitted.append
    session._loop = None
    session._router = None
    session._thread = None
    session._cancel = threading.Event()
    session._lock = threading.Lock()

    def fake_send(prompt: str) -> bool:
        def worker() -> None:
            time.sleep(0.01)
            # Goes through session.emit rather than appending directly, which
            # is what the real turn does -- and what the server's completion
            # signal wraps. Appending here would leave the stream waiting on an
            # event that never arrives.
            for ev in (
                {"type": "delta", "text": "hello "},
                {"type": "tool_start", "id": "c1", "name": "read_file", "arguments": {}},
                {"type": "tool_end", "id": "c1", "name": "read_file", "ok": True,
                 "summary": "main.py", "error": "", "elapsed_ms": 12.0},
                {"type": "done", "text": "hello ", "elapsed_s": 0.01, "chunks": 1,
                 "iterations": 1, "tool_calls": 1, "stop_reason": "stop",
                 "tokens": {"prompt": 10, "completion": 2, "cached": 0, "total": 12}},
            ):
                session.emit(ev)
            time.sleep(0.01)

        t = threading.Thread(target=worker, daemon=True)
        session._thread = t
        t.start()
        return True

    session.send = fake_send  # type: ignore[method-assign]

    httpd = srv.serve(session)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    yield base, emitted
    httpd.shutdown()
    httpd.server_close()


def get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def post_json(url: str, payload: dict, timeout: float = 10.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


class TestStatic:
    def test_index_served(self, live) -> None:
        base, _ = live
        status, body = get(f"{base}/")
        assert status == 200
        assert "<title>WUXICODE</title>" in body

    def test_assets_served(self, live) -> None:
        base, _ = live
        for path in ("/app.css", "/app.js", "/icon.ico"):
            status, body = get(f"{base}{path}")
            assert status == 200, path
            assert body, path

    def test_missing_file_is_404(self, live) -> None:
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as e:
            get(f"{base}/nope.css")
        assert e.value.code == 404

    def test_path_traversal_is_refused(self, live) -> None:
        """The server hands bytes to a process that runs tools. Escaping the UI
        directory would let a crafted URL read the source tree."""
        base, _ = live
        for evil in ("/../server.py", "/../../wx/cli.py", "/..%2fserver.py"):
            try:
                status, body = get(f"{base}{evil}")
            except urllib.error.HTTPError as e:
                assert e.code in (403, 404), f"{evil} -> {e.code}"
            else:
                assert "Session" not in body, f"{evil} leaked server.py"


class TestApi:
    def test_state(self, live) -> None:
        base, _ = live
        status, body = get(f"{base}/api/state")
        assert status == 200
        assert "workspace" in json.loads(body)

    def test_files_skips_noise_dirs(self, live) -> None:
        base, _ = live
        _, body = get(f"{base}/api/files")
        paths = [f["path"] for f in json.loads(body)["files"]]
        assert "main.py" in paths
        assert not any("node_modules" in p for p in paths)

    def test_empty_prompt_rejected(self, live) -> None:
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as e:
            post_json(f"{base}/api/chat", {"prompt": "   "})
        assert e.value.code == 400

    def test_bad_json_rejected(self, live) -> None:
        base, _ = live
        req = urllib.request.Request(
            f"{base}/api/chat", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 400


class TestStreaming:
    def test_turn_streams_sse_and_terminates(self, live) -> None:
        base, emitted = live
        req = urllib.request.Request(
            f"{base}/api/chat",
            data=json.dumps({"prompt": "hi"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            assert r.status == 200
            assert r.headers.get("Content-Type", "").startswith("text/event-stream")
            raw = r.read().decode("utf-8")

        frames = [ln[6:] for ln in raw.splitlines() if ln.startswith("data: ")]
        assert frames, "no SSE frames"
        events = [json.loads(f) for f in frames]
        kinds = [e["type"] for e in events]
        assert kinds[0] == "delta"
        assert "tool_start" in kinds and "tool_end" in kinds
        assert kinds[-1] == "end", "stream must terminate with an explicit end"

    def test_state_reports_busy_flag(self, live) -> None:
        """The send button and the live dot both read this, so the field has
        to exist even when nothing is running."""
        base, _ = live
        _, body = get(f"{base}/api/state")
        data = json.loads(body)
        assert "busy" in data
        assert data["busy"] is False

    def test_abort_endpoint(self, live) -> None:
        base, _ = live
        status, body = post_json(f"{base}/api/abort", {})
        assert status == 200
        assert json.loads(body)["ok"] is True
