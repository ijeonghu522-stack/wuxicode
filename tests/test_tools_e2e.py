"""Exercise the tool layer end to end against a real temp repository.

Every tool writes to disk, so most of them have only been unit-tested through
mocks. This runs them for real, in a temporary directory, and checks the two
properties that matter: they do what they claim, and they refuse to escape the
workspace root.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from wx.agent.tools import (  # noqa: E402
    BashTool,
    EditFileTool,
    ReadFileTool,
    WriteFileTool,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "code.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return tmp_path


class TestReadFile:
    def test_reads_text(self, ws: Path) -> None:
        out = run(ReadFileTool(workspace_root=ws).execute(path="a.txt"))
        assert "hello" in out and "world" in out

    def test_missing_file_is_an_error_string_not_an_exception(self, ws: Path) -> None:
        out = run(ReadFileTool(workspace_root=ws).execute(path="nope.txt"))
        # The agent loop feeds this straight to the model. A traceback here
        # becomes a model turn that dies, so tools must return prose.
        assert "Traceback" not in out
        assert "찾을 수 없" in out or "not found" in out.lower()

    def test_refuses_to_escape_the_workspace(self, ws: Path) -> None:
        secret = ws.parent / "secret.txt"
        secret.write_text("TOP SECRET", encoding="utf-8")
        out = run(ReadFileTool(workspace_root=ws).execute(path="../secret.txt"))
        assert "TOP SECRET" not in out
        assert "Traceback" not in out


class TestWriteFile:
    def test_writes(self, ws: Path) -> None:
        out = run(WriteFileTool(workspace_root=ws).execute(path="new.txt", content="x"))
        assert (ws / "new.txt").read_text(encoding="utf-8") == "x"
        assert "Traceback" not in out

    def test_creates_parent_directories(self, ws: Path) -> None:
        run(WriteFileTool(workspace_root=ws).execute(path="deep/nest/f.txt", content="y"))
        assert (ws / "deep" / "nest" / "f.txt").read_text(encoding="utf-8") == "y"

    def test_refuses_to_escape_the_workspace(self, ws: Path) -> None:
        out = run(WriteFileTool(workspace_root=ws).execute(path="../evil.txt", content="z"))
        assert not (ws.parent / "evil.txt").exists()
        assert "Traceback" not in out


class TestEditFile:
    def test_replaces_exact_text(self, ws: Path) -> None:
        out = run(
            EditFileTool(workspace_root=ws).execute(
                path="code.py", old_text="return 1", new_text="return 2"
            )
        )
        assert "return 2" in (ws / "code.py").read_text(encoding="utf-8")
        assert "Traceback" not in out

    def test_old_text_must_exist(self, ws: Path) -> None:
        before = (ws / "code.py").read_text(encoding="utf-8")
        out = run(
            EditFileTool(workspace_root=ws).execute(
                path="code.py", old_text="nowhere to be found", new_text="x"
            )
        )
        assert (ws / "code.py").read_text(encoding="utf-8") == before
        assert "Traceback" not in out

    def test_identical_old_and_new_is_rejected(self, ws: Path) -> None:
        out = run(
            EditFileTool(workspace_root=ws).execute(
                path="code.py", old_text="return 1", new_text="return 1"
            )
        )
        assert "동일" in out or "same" in out.lower()

    def test_missing_file_returns_prose_not_nameerror(self, ws: Path) -> None:
        """This path used to raise NameError: os was never imported."""
        out = run(
            EditFileTool(workspace_root=ws).execute(
                path="ghost.py", old_text="a", new_text="b"
            )
        )
        assert "NameError" not in out
        assert "Traceback" not in out


class TestBashTool:
    def test_runs_a_command(self, ws: Path) -> None:
        out = run(BashTool(workspace_root=ws).execute(command="echo hello"))
        assert "hello" in out

    def test_rejects_dangerous_commands(self, ws: Path) -> None:
        out = run(BashTool(workspace_root=ws).execute(command="rm -rf /"))
        assert "오류" in out or "error" in out.lower()

    def test_debug_output_is_gated(self, ws: Path, monkeypatch) -> None:
        """The debug print used to fire on every command, unconditionally."""
        import io

        monkeypatch.delenv("WX_DEBUG", raising=False)
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stderr", buf)
        run(BashTool(workspace_root=ws).execute(command="echo quiet"))
        assert "Executing command" not in buf.getvalue()

        buf2 = io.StringIO()
        monkeypatch.setattr(sys, "stderr", buf2)
        monkeypatch.setenv("WX_DEBUG", "1")
        run(BashTool(workspace_root=ws).execute(command="echo loud"))
        assert "Executing command" in buf2.getvalue()
