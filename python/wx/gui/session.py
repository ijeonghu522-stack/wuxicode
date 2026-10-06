"""The agent, owned by the window instead of by a terminal.

The CLI builds a router, a loop, and a turn's events, then prints them. This
does the same and hands the events to a callback instead. Nothing about the
agent is reimplemented here, so a fix in wx.agent.loop shows up in the GUI on
the next launch rather than needing to be written twice.

One turn at a time. A second request while a turn is running is refused rather
than queued: two concurrent turns would interleave their deltas into one
message, and the user cannot tell which text belongs to which question.
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from wx.agent.events import (
    Error,
    Notice,
    StreamInterrupted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnDone,
    Usage,
)
from wx.agent.loop import AgentLoop
from wx.agent.tools import bash, edit_file, read_file, write_file
from wx.config import load_config  # noqa: F401  (re-exported for convenience)
from wx.llm.router import load_router_config, ModelRouter

Event = dict[str, Any]
Emit = Callable[[Event], None]

#: Events rendered as their own line in the transcript rather than folded into
#: the assistant bubble. Kept here rather than in the browser so the window and
#: the CLI cannot disagree about what a turn consists of.
TRANSCRIPT_EVENTS = (Notice, Error, StreamInterrupted)


@dataclass
class SessionState:
    model: str = ""
    provider: str = ""
    base_url: str = ""
    workspace: str = ""
    has_key: bool = False
    busy: bool = False


@dataclass
class Session:
    """One conversation against one workspace."""

    workspace: Path
    emit: Emit = field(default=lambda _e: None)
    _loop: AgentLoop | None = None
    _router: Any = None
    _thread: threading.Thread | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self._build()

    # -- construction -----------------------------------------------------

    def _tools(self) -> list[Any]:
        root = str(self.workspace)
        return [
            bash.BashTool(workspace_root=root),
            read_file.ReadFileTool(workspace_root=root),
            write_file.WriteFileTool(workspace_root=root),
            edit_file.EditFileTool(workspace_root=root),
        ]

    def _build(self) -> None:
        cfg = load_router_config()
        self._router = ModelRouter(cfg)
        self._loop = AgentLoop(
            self._router,
            system_prompt=None,
            tools=self._tools(),
            workspace_root=str(self.workspace),
        )

    # -- state ------------------------------------------------------------

    def state(self) -> SessionState:
        cfg = self._router.config if self._router else load_router_config()
        return SessionState(
            model=cfg.primary_model,
            provider=cfg.provider,
            base_url=cfg.base_url,
            workspace=str(self.workspace),
            has_key=bool(cfg.api_key),
            busy=self.busy(),
        )

    def busy(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # -- project listing ---------------------------------------------------

    #: Directories never worth sending to the file panel. Chosen for noise, not
    #: taste: a dependency tree has thousands of entries that push the actual
    #: source files off the first screen.
    SKIP_DIRS = frozenset({
        ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
        ".idea", ".vscode", "target", ".next", ".cache",
    })
    MAX_FILES = 4000

    def files(self) -> list[dict[str, Any]]:
        """Project files for the sidebar filter. Never raises.

        A workspace the user cannot read -- a permission error on the root, a
        symlink loop, a drive that went away -- is a normal state for a panel,
        not a reason to take the window down.
        """
        out: list[dict[str, Any]] = []
        root = self.workspace
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
                for name in filenames:
                    full = Path(dirpath) / name
                    try:
                        size = full.stat().st_size
                    except OSError:
                        continue
                    rel = full.relative_to(root).as_posix()
                    out.append({"path": rel, "size": size})
                    if len(out) >= self.MAX_FILES:
                        return out
        except (OSError, ValueError):
            return out
        return out

    # -- one turn ---------------------------------------------------------

    def send(self, prompt: str) -> bool:
        """Start a turn in a worker thread. False if one is already running."""
        prompt = (prompt or "").strip()
        if not prompt:
            return False
        with self._lock:
            if self.busy():
                return False
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run_turn,
                args=(prompt,),
                name="wx-gui-turn",
                daemon=True,
            )
            self._thread.start()
        return True

    def abort(self) -> None:
        self._cancel.set()

    def _run_turn(self, prompt: str) -> None:
        assert self._loop is not None
        try:
            asyncio.run(self._drive(prompt))
        except Exception as exc:  # a window must survive a bad turn
            self.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    async def _drive(self, prompt: str) -> None:
        loop = self._loop
        assert loop is not None

        async for ev in loop.run(prompt):
            if self._cancel.is_set():
                self.emit({"type": "aborted"})
                break
            self.emit(_encode(ev))

    def shutdown(self) -> None:
        self._cancel.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=5.0)


def _encode(ev: object) -> Event:
    """Agent event -> JSON-safe dict.

    dataclasses.asdict is deliberately not used: it recurses into nested
    structures the browser has no use for, and it is the kind of convenience
    that breaks silently when a field is added upstream.
    """
    if isinstance(ev, TextDelta):
        return {"type": "delta", "text": ev.text}
    if isinstance(ev, ToolCallStarted):
        return {
            "type": "tool_start",
            "id": ev.call_id,
            "name": ev.name,
            "arguments": ev.arguments,
        }
    if isinstance(ev, ToolCallFinished):
        return {
            "type": "tool_end",
            "id": ev.call_id,
            "name": ev.name,
            "ok": ev.ok,
            "summary": getattr(ev, "summary", "") or "",
            "error": getattr(ev, "error", "") or "",
            "elapsed_ms": round(getattr(ev, "elapsed_ms", 0.0), 1),
        }
    if isinstance(ev, Usage):
        return {"type": "usage", "usage": _usage(ev)}
    if isinstance(ev, TurnDone):
        return {
            "type": "done",
            "text": ev.text,
            "elapsed_s": round(ev.elapsed_s, 2),
            "chunks": ev.chunks,
            "iterations": ev.iterations,
            "tool_calls": ev.tool_calls,
            "stop_reason": ev.stop_reason or "",
            "tokens": _usage(ev.usage),
        }
    if isinstance(ev, StreamInterrupted):
        return {
            "type": "interrupted",
            "partial_text": ev.partial_text,
            "message": ev.message,
            "retryable": ev.retryable,
        }
    if isinstance(ev, Notice):
        return {"type": "notice", "message": ev.text, "level": ev.level}
    if isinstance(ev, Error):
        return {
            "type": "error",
            "message": ev.message,
            "kind": ev.kind,
            "retryable": ev.retryable,
        }
    return {"type": "unknown", "repr": repr(ev)}


def _usage(u: object) -> dict[str, Any] | None:
    """Usage -> dict, keyed by the names the dataclass actually uses.

    prompt_tokens / completion_tokens / cached_tokens, not input/output: the
    earlier spelling of this function invented its own field names and would
    have reported zero tokens against a perfectly good response.
    """
    if u is None:
        return None
    return {
        "prompt": getattr(u, "prompt_tokens", 0),
        "completion": getattr(u, "completion_tokens", 0),
        "cached": getattr(u, "cached_tokens", 0),
        "total": getattr(u, "total_tokens", 0),
    }


def default_workspace() -> Path:
    return Path(os.getcwd())
