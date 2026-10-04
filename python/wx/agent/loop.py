"""WUXICODE — the agent loop.

A state machine and nothing else. It owns message order, termination and
accounting. It does not print, it does not touch a terminal, and it does not
decide which tools run — Phase 2 attaches the scheduler, Phase 4 attaches a
renderer, and this file does not change.

Phase 1A runs a single pass per turn: one request, no tool loop, because there
are no tools yet. `max_iterations` is wired in now and is not a stub — the loop
terminates correctly when it is exceeded, which is the part that has to be
right before tools exist.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from typing import Any

from wx.agent.events import (
    Error,
    Event,
    Notice,
    StreamInterrupted,
    TextDelta,
    TurnDone,
    Usage,
)
from wx.llm.openai_client import (
    DoneChunk,
    OpenAIClient,
    ReasoningChunk,
    StreamError,
    TextChunk,
    ToolCallDelta,
    UsageChunk,
)

__all__ = ["AgentLoop", "DEFAULT_SYSTEM_PROMPT"]

DEFAULT_SYSTEM_PROMPT = (
    "You are WX, a coding agent working in a user's local codebase. "
    "Prefer reading files before changing them. Be direct: no preamble, no "
    "summary of what you are about to do. State what changed and why."
)

# Phase 1A completes a turn in one pass. The cap exists so that when the tool
# loop arrives it cannot spin forever.
DEFAULT_MAX_ITERATIONS = 1


class AgentLoop:
    def __init__(
        self,
        client: OpenAIClient,
        *,
        system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tokenizer: Any = None,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.max_iterations = max(1, max_iterations)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._tokenizer = tokenizer

    # -- token accounting ---------------------------------------------------

    @property
    def tokenizer(self) -> Any:
        """The native core, loaded on first use.

        Phase 3 replaces the heuristic with tiktoken and this property is the
        only place that has to change.
        """
        if self._tokenizer is None:
            from wx import require_core

            self._tokenizer = require_core()
        return self._tokenizer

    @property
    def tokenizer_mode(self) -> str:
        try:
            return str(self.tokenizer.tokenizer_mode())
        except Exception:
            return "unavailable"

    # -- history ------------------------------------------------------------

    def initial_messages(self, user_input: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_input})
        return messages

    def history_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Approximate tokens already occupied by the message list.

        Counts content only. Per-message protocol overhead (~3-4 tokens) is
        owned by `context/assembly.py` from Phase 3, where the budget is
        actually enforced.
        """
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total += int(self.tokenizer.count_tokens(content))
        return total

    # -- turn ---------------------------------------------------------------

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        """Execute one turn and yield events as they happen.

        Always terminates with `TurnDone` or `Error`. Never raises for an
        expected failure — a dropped connection is an event the UI renders,
        not a traceback it has to survive.

        If the upstream stream is interrupted (504, connection reset),
        yields `StreamInterrupted` with partial text, then `TurnDone` with
        the partial text marked incomplete. The session continues normally.
        """
        messages = self.initial_messages(user_input)
        started = time.perf_counter()
        chunks = 0
        pieces: list[str] = []
        usage: Usage | None = None
        stop_reason: str | None = None
        interrupted = False
        interrupt_message = ""
        interrupt_retryable = True

        try:
            stream = self.client.stream(
                messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            # Show reasoning only if WX_DEBUG=1 or WX_SHOW_REASONING=1
            _show_reasoning = os.environ.get("WX_DEBUG") == "1" or os.environ.get("WX_SHOW_REASONING") == "1"

            async for ev in stream:
                if isinstance(ev, TextChunk):
                    pieces.append(ev.text)
                    chunks += 1
                    yield TextDelta(ev.text)
                elif isinstance(ev, ReasoningChunk):
                    if _show_reasoning:
                        yield Notice(f"[reasoning] {ev.text}", level="info")
                elif isinstance(ev, ToolCallDelta):
                    # No tools are registered in Phase 1A. Surface the call
                    # instead of dropping it: a provider that emits tool calls
                    # at this stage is a configuration error worth seeing.
                    yield Notice(
                        f"tool call received but no tools are registered "
                        f"(phase 1A): {ev.name or '<unnamed>'}",
                        level="warn",
                    )
                elif isinstance(ev, UsageChunk):
                    usage = ev.usage
                    yield usage
                elif isinstance(ev, DoneChunk):
                    stop_reason = ev.stop_reason
                elif isinstance(ev, StreamInterrupted):
                    # Stream cut off mid-response — mark as interrupted
                    interrupted = True
                    interrupt_message = ev.message
                    interrupt_retryable = ev.retryable
                    # The partial text is already in pieces (from TextChunk events)
                    yield StreamInterrupted(
                        partial_text=ev.partial_text,
                        message=ev.message,
                        retryable=ev.retryable,
                    )
                    break  # Exit stream loop, but continue to TurnDone

        except StreamError as exc:
            yield Error(
                message=str(exc),
                kind=exc.kind,
                retryable=exc.retryable,
            )
            yield TurnDone(
                text="".join(pieces),
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=chunks,
            )
            return

        except (OSError, ValueError) as exc:
            yield Error(message=f"{type(exc).__name__}: {exc}", kind="internal")
            yield TurnDone(
                text="".join(pieces),
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=chunks,
            )
            return

        text = "".join(pieces)
        if interrupted:
            stop_reason = "interrupted"
            # Mark the turn as incomplete but allow session to continue
            yield TurnDone(
                text=text,
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=chunks,
            )
        else:
            yield TurnDone(
                text=text,
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=chunks,
            )
        self._history = messages