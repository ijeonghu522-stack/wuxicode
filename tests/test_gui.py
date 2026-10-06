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
from wx.gui.pool import SessionPool  # noqa: E402
from wx.gui.session import Session  # noqa: E402


def scripted_session(workspace: Path, emitted: list[dict], text: str = "hello ") -> Session:
    """A Session with a real interface and a fake turn.

    Built with __new__ so AgentLoop is never constructed: the HTTP layer is
    what is under test, and a real loop would need a model to answer.
    """
    session = Session.__new__(Session)
    session.workspace = workspace
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
                {"type": "delta", "text": text},
                {"type": "tool_start", "id": "c1", "name": "read_file", "arguments": {}},
                {"type": "tool_end", "id": "c1", "name": "read_file", "ok": True,
                 "summary": "main.py", "error": "", "elapsed_ms": 12.0},
                {"type": "done", "text": text, "elapsed_s": 0.01, "chunks": 1,
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
    return session


def slow_session(workspace: Path) -> Session:
    """A session whose turn stays running until the test lets it finish.

    `busy()` is what the server's 409 check reads, and a scripted turn that
    finishes in ten milliseconds is already idle by the time the next request
    arrives -- so the concurrency guard has nothing to be concurrent with.
    """
    session = Session.__new__(Session)
    session.workspace = workspace
    session.emit = lambda _e: None
    session._loop = None
    session._router = None
    session._thread = None
    session._cancel = threading.Event()
    session._lock = threading.Lock()
    release = threading.Event()

    def fake_send(prompt: str) -> bool:
        def worker() -> None:
            release.wait(timeout=20)
            session.emit({"type": "aborted"})

        t = threading.Thread(target=worker, daemon=True)
        session._thread = t
        t.start()
        return True

    session.send = fake_send  # type: ignore[method-assign]
    session._release = release  # type: ignore[attr-defined]
    return session


def stream_events(base: str, tab: int, prompt: str = "hi", timeout: float = 20.0) -> list[dict]:
    """POST a turn and read the SSE frames the browser would have painted."""
    req = urllib.request.Request(
        f"{base}/api/chat?tab={tab}",
        data=json.dumps({"prompt": prompt}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return [json.loads(ln[6:]) for ln in raw.splitlines() if ln.startswith("data: ")]


@pytest.fixture()
def live(tmp_path: Path):
    """A running server over a pool with two scripted tabs."""
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")

    emitted: list[dict] = []
    emitted2: list[dict] = []

    pool = SessionPool(workspace=tmp_path)
    # Registered directly rather than through get(): lazy creation would build
    # a real AgentLoop on first request.
    pool._sessions[1] = scripted_session(tmp_path, emitted, "hello ")
    pool._sessions[2] = scripted_session(tmp_path, emitted2, "second ")
    pool._order.extend([1, 2])

    httpd = srv.serve(pool)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    # The pool is yielded so a test can install a session it controls; the
    # other three are the shapes most tests want.
    yield base, pool, emitted, emitted2
    httpd.shutdown()
    httpd.server_close()
    pool.shutdown()


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
        base, *_ = live
        status, body = get(f"{base}/")
        assert status == 200
        assert "<title>WUXICODE</title>" in body

    def test_assets_served(self, live) -> None:
        base, *_ = live
        for path in ("/app.css", "/app.js", "/icon.ico"):
            status, body = get(f"{base}{path}")
            assert status == 200, path
            assert body, path

    def test_missing_file_is_404(self, live) -> None:
        base, *_ = live
        with pytest.raises(urllib.error.HTTPError) as e:
            get(f"{base}/nope.css")
        assert e.value.code == 404

    def test_path_traversal_is_refused(self, live) -> None:
        """The server hands bytes to a process that runs tools. Escaping the UI
        directory would let a crafted URL read the source tree."""
        base, *_ = live
        for evil in ("/../server.py", "/../../wx/cli.py", "/..%2fserver.py"):
            try:
                status, body = get(f"{base}{evil}")
            except urllib.error.HTTPError as e:
                assert e.code in (403, 404), f"{evil} -> {e.code}"
            else:
                assert "Session" not in body, f"{evil} leaked server.py"


class TestApi:
    def test_state(self, live) -> None:
        base, *_ = live
        status, body = get(f"{base}/api/state")
        assert status == 200
        assert "workspace" in json.loads(body)

    def test_files_skips_noise_dirs(self, live) -> None:
        base, *_ = live
        _, body = get(f"{base}/api/files")
        paths = [f["path"] for f in json.loads(body)["files"]]
        assert "main.py" in paths
        assert not any("node_modules" in p for p in paths)

    def test_empty_prompt_rejected(self, live) -> None:
        base, *_ = live
        with pytest.raises(urllib.error.HTTPError) as e:
            post_json(f"{base}/api/chat", {"prompt": "   "})
        assert e.value.code == 400

    def test_bad_json_rejected(self, live) -> None:
        base, *_ = live
        req = urllib.request.Request(
            f"{base}/api/chat", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 400


class TestTabs:
    """Each tab is its own agent.

    The bug this guards is not a 500: it is two answers rendered into the wrong
    conversation, which no status code reports.
    """

    def test_tabs_get_separate_sessions(self, live) -> None:
        """Two tabs, two conversations.

        Read off the streams rather than the fixture's own list: the server
        swaps session.emit for its queue while a turn is draining, so what the
        browser sees is the only thing that proves the routing.
        """
        base, *_ = live
        one = stream_events(base, 1, "one")
        two = stream_events(base, 2, "two")
        assert [e["text"] for e in one if e["type"] == "delta"] == ["hello "]
        assert [e["text"] for e in two if e["type"] == "delta"] == ["second "]

    def test_second_turn_on_a_busy_tab_rejected(self, live) -> None:
        """Not queued.

        Two concurrent turns on one session interleave their deltas into one
        message, and the user cannot tell which question a span of text answers.
        So the second request is refused while the first is still running.
        """
        base, pool, *_ = live
        slow = slow_session(pool.workspace)
        pool._sessions[3] = slow
        pool._order.append(3)

        started: list[str] = []
        reader = threading.Thread(
            target=lambda: started.append(str(stream_events(base, 3, "long")[0].get("type"))),
            daemon=True,
        )
        reader.start()
        # Wait for the worker to actually be alive rather than guessing at a
        # sleep: the check under test is busy(), and busy() is about the thread.
        for _ in range(200):
            if slow.busy():
                break
            time.sleep(0.01)
        assert slow.busy(), "the scripted turn never started"

        with pytest.raises(urllib.error.HTTPError) as e:
            post_json(f"{base}/api/chat?tab=3", {"prompt": "again"}, timeout=20)
        assert e.value.code == 409

        slow._release.set()  # type: ignore[attr-defined]
        reader.join(timeout=10)
        assert started, "the first turn never finished"

    def test_unknown_tab_gets_its_own_session(self, live) -> None:
        base, *_ = live
        status, body = get(f"{base}/api/state?tab=9")
        assert status == 200
        assert "workspace" in json.loads(body)

    def test_junk_tab_id_falls_back(self, live) -> None:
        base, *_ = live
        status, body = get(f"{base}/api/state?tab=not-a-number")
        assert status == 200
        assert json.loads(body)["workspace"]

    def test_close_drops_only_that_tab(self, live) -> None:
        base, *_ = live
        _, body = post_json(f"{base}/api/tab/close?tab=2", {})
        assert json.loads(body)["closed"] is True
        # Tab 2 is recreated on demand, tab 1 is untouched.
        assert get(f"{base}/api/state?tab=1")[0] == 200
        assert get(f"{base}/api/state?tab=2")[0] == 200

    def test_close_unknown_tab_reports_false(self, live) -> None:
        base, *_ = live
        _, body = post_json(f"{base}/api/tab/close?tab=77", {})
        assert json.loads(body)["closed"] is False


class TestCode:
    def test_file_read(self, live) -> None:
        base, *_ = live
        _, body = get(f"{base}/api/file?path=main.py")
        data = json.loads(body)
        assert data["ok"] is True
        assert "print('hi')" in data["text"]
        assert data["lang"] == "python"

    def test_file_outside_workspace_refused(self, live) -> None:
        """The file panel reads bytes for a process that runs shell tools. Path
        containment is the only thing between it and C:\\Users\\...\\.ssh."""
        base, *_ = live
        _, body = get(f"{base}/api/file?path=../../../../Windows/win.ini")
        assert json.loads(body)["ok"] is False

    def test_git_panels_on_a_non_repo(self, live) -> None:
        """A workspace without git is normal; the panels must say so instead of
        failing."""
        base, *_ = live
        for path in ("/api/git/status", "/api/git/diff", "/api/git/log"):
            status, body = get(f"{base}{path}")
            assert status == 200, path
            assert isinstance(json.loads(body), dict), path

    def test_diff_of_a_real_repo(self, tmp_path: Path) -> None:
        import subprocess

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(tmp_path), *args], check=True,
                capture_output=True,
                env={**dict(__import__("os").environ),
                     "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"},
            )

        (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
        try:
            git("init", "-q")
            git("add", ".")
            git("commit", "-qm", "first")
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("git unavailable")

        (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")
        pool = SessionPool(workspace=tmp_path)
        from wx.gui import pool as poolmod
        status = poolmod.git_status(tmp_path)
        assert status["repo"] is True
        assert status["unstaged"] == 1
        diff = poolmod.git_diff(tmp_path)
        assert diff["ok"] is True
        assert "-one" in diff["diff"] and "+two" in diff["diff"]
        log = poolmod.git_log(tmp_path)
        assert log["commits"][0]["subject"] == "first"
        pool.shutdown()


class TestStreaming:
    def test_turn_streams_sse_and_terminates(self, live) -> None:
        base, *_ = live
        req = urllib.request.Request(
            f"{base}/api/chat?tab=1",
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
        base, *_ = live
        _, body = get(f"{base}/api/state")
        data = json.loads(body)
        assert "busy" in data
        assert data["busy"] is False

    def test_abort_endpoint(self, live) -> None:
        base, *_ = live
        status, body = post_json(f"{base}/api/abort?tab=1", {})
        assert status == 200
        # Aborting an idle tab is a no-op that must not close the session: the
        # transcript behind that tab is still the user's.
        assert json.loads(body)["ok"] is True
        assert get(f"{base}/api/state?tab=1")[0] == 200
