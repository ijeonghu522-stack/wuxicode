"""WUXICODE — agent event types.

The agent loop yields events. It never prints, never writes to a terminal, and
never knows whether a TUI, a pipe, or a log file is on the other end. Phase 4
adds an FTXUI consumer of this stream; `wuxicode "hello" | cat` consumes exactly
the same events without a single escape sequence.

Tool events (`ToolCallStarted`, `ToolCallFinished`) are declared here together
with the scheduler that produces them. A renderer that does not display them
still type-checks, because the union is closed and `EVENT_TYPES` is the
single place a consumer enumerates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

__all__ = [
    "Event",
    "TextDelta",
    "ReasoningDelta",
    "Usage",
    "Notice",
    "Error",
    "TurnDone",
    "StreamInterrupted",
    "ToolCallStarted",
    "ToolCallFinished",
    "EVENT_TYPES",
]


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A fragment of assistant output. Order is the contract."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Model reasoning content, when the provider exposes it.

    Kept separate from `TextDelta` so a renderer can show it dimmed, fold it
    away, or refuse to display it — without inspecting payload text.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0

    @property
    def fresh_prompt_tokens(self) -> int:
        """Prompt tokens not served from the provider's cache."""
        return max(0, self.prompt_tokens - self.cached_tokens)


@dataclass(frozen=True, slots=True)
class Notice:
    """Something the user should see that is not model output.

    Used for degradation that must stay visible: "tokenizer fell back to
    heuristic", "WX AI substituted for an unavailable slot", "cache cleared".
    Silently downgrading is how a budget decision gets made against a number
    that is not what it claims to be.
    """

    text: str
    level: Literal["info", "warn"] = "info"


@dataclass(frozen=True, slots=True)
class Error:
    """A failure the turn could not recover from."""

    message: str
    kind: str = "unknown"
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class StreamInterrupted:
    """Stream was cut off mid-response (upstream timeout, connection reset).

    Partial output was delivered before the interruption. The session continues;
    the next turn proceeds normally. The UI should mark the partial output as
    incomplete but not abort the session.
    """

    partial_text: str
    message: str
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool is about to run.

    Yielded before execution so a renderer can show "running pytest" during a
    thirty-second call rather than after it. `arguments` is the parsed object
    when it parsed and a `{"__raw__": ...}` wrapper when it did not, which is
    the same shape `TurnRecord.resolved_calls` produces.
    """

    call_id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)
    iteration: int = 0


@dataclass(frozen=True, slots=True)
class ToolCallFinished:
    """A tool finished.

    `error` is a short human-facing reason rather than the full exception
    chain. The chain belongs in the result text the model sees; a terminal
    wants one line. Truncating here also keeps a traceback from scrolling the
    model's own output off the screen.
    """

    call_id: str
    name: str
    ok: bool
    summary: str = ""
    error: str | None = None
    elapsed_ms: float = 0.0
    iteration: int = 0


@dataclass(frozen=True, slots=True)
class TurnDone:
    """End of turn. Always the last event of a turn, on success and on failure."""

    text: str
    usage: Usage | None = None
    stop_reason: str | None = None
    elapsed_s: float = 0.0
    chunks: int = 0
    iterations: int = 1
    tool_calls: int = 0


Event: TypeAlias = (
    TextDelta
    | ReasoningDelta
    | Usage
    | Notice
    | Error
    | TurnDone
    | StreamInterrupted
    | ToolCallStarted
    | ToolCallFinished
)

# Concrete types, for consumers that filter by `type(ev)`.
EVENT_TYPES: tuple[type, ...] = (
    TextDelta,
    ReasoningDelta,
    Usage,
    Notice,
    Error,
    TurnDone,
    StreamInterrupted,
    ToolCallStarted,
    ToolCallFinished,
)