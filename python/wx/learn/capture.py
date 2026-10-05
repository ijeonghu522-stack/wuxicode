"""WUXICODE — turn capture.

The write side of learning. Everything the agent does flows through here so
that `extract` has something to distil and `digest` has something to report on.

CAPTURE IS BEST-EFFORT BY CONSTRUCTION. Every public method swallows its own
exceptions and returns a falsy value. The reason is structural: this module
sits inside the request path of a turn the user is waiting on. A learning layer
that can raise into the agent loop is a learning layer that eventually takes
down a session at the worst moment, and the value it adds never justifies
that trade.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from wx.learn.store import LearnStore

__all__ = ["Capture", "TurnRecord"]


@dataclass(slots=True)
class TurnRecord:
    """Accumulator for one assistant turn.

    Tool calls arrive as streaming fragments and are reassembled by index.
    `parallel_tool_calls` is disabled at the client, but fragments are still
    indexed rather than appended, because a provider that ignores the flag
    sends interleaved deltas and concatenating by arrival order would execute
    one call with another's arguments.
    """

    turn_id: int | None = None
    session_id: str = ""
    text_parts: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    def add_text(self, text: str) -> None:
        if text:
            self.text_parts.append(text)

    def add_tool_delta(self, index: int, name: str | None, args_fragment: str, call_id: str | None) -> None:
        slot = self.tool_calls.get(index)
        if slot is None:
            slot = {"id": call_id or "", "name": name or "", "arguments": ""}
            self.tool_calls[index] = slot
        if call_id:
            slot["id"] = call_id
        if name:
            slot["name"] = name
        if args_fragment:
            slot["arguments"] += args_fragment

    def resolved_calls(self) -> list[dict[str, Any]]:
        """Completed calls in index order, with arguments parsed when possible.

        Malformed JSON is kept as a raw string rather than dropped: the failure
        itself is a signal worth recording, and an unreadable argument blob is
        often exactly what `extract` needs to see.
        """
        out: list[dict[str, Any]] = []
        for index in sorted(self.tool_calls):
            slot = self.tool_calls[index]
            raw = slot.get("arguments") or ""
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {"__raw__": raw}
            out.append(
                {
                    "id": slot.get("id") or f"call_{index}",
                    "name": slot.get("name") or "unknown",
                    "arguments": parsed if isinstance(parsed, dict) else {"value": parsed},
                    "raw": raw,
                }
            )
        return out

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


class Capture:
    """Session-scoped recorder.

    One instance per CLI session. Holds the open turn so the caller does not
    have to thread partial tool-call state through its own code.
    """

    #: Longest tool result kept in the store. Beyond this the row costs more
    #: than it teaches — a 40KB traceback tells `extract` less than its first
    #: three lines, which is what the failure patterns are built from.
    MAX_RESULT_CHARS = 4000

    def __init__(self, store: LearnStore, *, session_id: str | None = None, cwd: str | None = None) -> None:
        self.store = store
        self.session_id = session_id or uuid.uuid4().hex[:16]
        self.cwd = cwd or os.getcwd()
        self.turns: list[TurnRecord] = []
        self._current: TurnRecord | None = None
        self.enabled = True
        self._start()

    def _start(self) -> None:
        try:
            self.store.start_session(self.session_id, self.cwd)
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------

    def begin_turn(self) -> TurnRecord:
        """Open a turn and record the user message that prompted it."""
        record = TurnRecord(session_id=self.session_id)
        self._current = record
        self.turns.append(record)
        return record

    def set_user_text(self, text: str) -> None:
        if self._current is not None:
            record = self._current
        else:
            record = self.begin_turn()
        record.turn_id = self.store.record_turn(self.session_id, "user", text)

    def record_assistant(self, text: str) -> int:
        if self._current is None:
            self.begin_turn()
        assert self._current is not None
        return self.store.record_turn(self.session_id, "assistant", text)

    def end_turn(self) -> TurnRecord | None:
        record = self._current
        self._current = None
        return record

    def close(self) -> dict[str, Any]:
        """Flush and close the session row."""
        summary = {
            "session_id": self.session_id,
            "turns": len(self.turns),
            "tool_calls": sum(len(t.tool_calls) for t in self.turns),
        }
        try:
            self.store.end_session(self.session_id, len(self.turns))
        except Exception:
            pass
        return summary

    # -- writes -------------------------------------------------------------

    def record_tool_result(
        self,
        *,
        name: str,
        arguments: dict[str, Any] | str,
        result: str,
        ok: bool = True,
    ) -> int:
        """Persist one tool outcome.

        This is the highest-value row in the system. `extract` reads failures
        to build antipatterns and repeated successes to build recipes, and
        neither works if this method is skipped.
        """
        if isinstance(arguments, dict):
            args_blob = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        else:
            args_blob = str(arguments)
        turn_id = self._current.turn_id if self._current else None
        return self.store.record_tool_call(
            name=name,
            args=args_blob,
            result=result[: self.MAX_RESULT_CHARS],
            ok=ok,
            turn_id=turn_id,
            session_id=self.session_id,
        )

    def record_tool_error(
        self,
        *,
        name: str,
        arguments: dict[str, Any] | str,
        error: str,
    ) -> int:
        return self.record_tool_result(name=name, arguments=arguments, result=error, ok=False)

    def note(self, kind: str, text: str, source: str = "") -> int:
        """Write an observation directly, bypassing extraction.

        For things the runtime learns rather than infers — the user said so
        explicitly, or a provider returned a capability the agent now knows
        about. Extraction is statistical; this is declarative.
        """
        return self.store.add_observation(kind, text, source or f"session:{self.session_id}")

    def forget(self, kind: str, text: str) -> bool:
        """Remove an observation. Backs the `/wx forget` style commands."""
        try:
            with self.store._tx() as conn:  # noqa: SLF001 - same package
                row = conn.execute(
                    "SELECT id FROM observations WHERE kind=? AND text=?", (kind, text)
                ).fetchone()
                if not row:
                    return False
                conn.execute("DELETE FROM observations WHERE id=?", (row["id"],))
                conn.execute("DELETE FROM obs_fts WHERE rowid=?", (row["id"],))
            return True
        except Exception:
            return False

    def __enter__(self) -> Capture:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()