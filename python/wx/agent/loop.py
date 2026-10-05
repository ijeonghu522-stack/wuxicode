"""WUXICODE — the agent loop.

A state machine and nothing else. It owns message order, termination and
accounting. It does not print, it does not touch a terminal, and it does not
decide how tools are rendered — Phase 4 attaches a renderer, and this file
does not change.

THE LOOP IS NOW REAL. It runs model → tools → model until the model stops
asking for tools or the iteration budget runs out. Two things were decided
here rather than left to the model, and both are safety properties:

    MAX_ITERATIONS bounds the cycle. A model that keeps calling `bash` because
    each result tells it to try something else will run forever otherwise. The
    default is generous enough for a genuine investigation and small enough
    that a stuck agent ends in seconds rather than in someone's GPU budget.

    TOOL_TIMEOUT bounds each tool. A build that takes two minutes is normal; a
    build that waits on a prompt forever is not, and a terminal with no way to
    interrupt a turn is worse than one that admits it gave up.

TOOLS ARE OPTIONAL. With no tools registered the loop is a single pass, which
is exactly what it was before tools existed. That is deliberate: the tool-free
path is the one that has to work when a provider rejects a `tools` parameter,
and it is the path used by `chat` mode.

LEARNING IS INJECTED, NOT OWNED. This file asks an `Injector` for a system
prompt and tells a `Capture` what happened. It never imports SQLite and never
knows that observations have kinds. Both are optional and both degrade to
no-ops, so a build without the learning layer behaves identically to one with
it.
"""

from __future__ import annotations

import asyncio
import json
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
    ToolCallFinished,
    ToolCallStarted,
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

__all__ = ["AgentLoop", "DEFAULT_SYSTEM_PROMPT", "DEFAULT_MAX_ITERATIONS", "DEFAULT_TOOL_TIMEOUT"]

DEFAULT_SYSTEM_PROMPT = (
    "You are WX, a coding agent working in a user's local codebase. "
    "Prefer reading files before changing them. Be direct: no preamble, no "
    "summary of what you are about to do. State what changed and why."
)

#: Enough for a real investigation (read, grep, read, edit, test) with room to
#: react to one failure. High enough that normal work never hits it, low
#: enough that a loop is visible within a minute.
DEFAULT_MAX_ITERATIONS = 12

#: Seconds before a single tool call is abandoned. Long enough for a release
#: build, short enough that a hung process is not mistaken for a hung agent.
DEFAULT_TOOL_TIMEOUT = 300.0

#: Tool output pasted into the conversation. Beyond this the model is reading a
#: log, not a result, and the tail that explains the failure gets truncated
#: away — so the truncation keeps both ends.
TOOL_RESULT_LIMIT = 6000


def _clip(text: str, limit: int = TOOL_RESULT_LIMIT) -> str:
    """Trim tool output, keeping head and tail.

    Head carries the command that ran, tail carries the error. A plain
    `text[:limit]` keeps the head and throws away the only part that says what
    went wrong.
    """
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head - 24
    dropped = len(text) - head - tail
    return f"{text[:head]}\n… ({dropped}자 생략) …\n{text[-tail:]}"


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
        tools: list[Any] | None = None,
        tool_timeout: float = DEFAULT_TOOL_TIMEOUT,
        injector: Any | None = None,
        capture: Any | None = None,
        session_id: str | None = None,
        workspace_root: str | None = None,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.max_iterations = max(1, max_iterations)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._tokenizer = tokenizer

        self.tools = list(tools or [])
        self._tools_by_name = {t.name: t for t in self.tools}
        self.tool_timeout = tool_timeout

        # Learning. Both optional; both may be any object exposing the small
        # surface used here, so the loop does not depend on their concrete
        # types and a test can pass a two-line stub.
        self.injector = injector
        self.capture = capture
        self.session_id = session_id
        self.workspace_root = workspace_root or os.getcwd()

        self._history: list[dict[str, Any]] = []

    # -- token accounting ---------------------------------------------------

    @property
    def tokenizer(self) -> Any:
        """The native core, loaded on first use."""
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

    # -- tools --------------------------------------------------------------

    @property
    def has_tools(self) -> bool:
        return bool(self.tools)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI schemas for every registered tool.

        A tool whose `schema()` raises is dropped with a notice rather than
        taking the turn down: one malformed tool is a configuration mistake,
        and the other six still work.
        """
        schemas: list[dict[str, Any]] = []
        for tool in self.tools:
            try:
                schemas.append(tool.schema())
            except Exception as exc:
                print(f"[wx] tool {getattr(tool, 'name', '?')} schema failed: {exc}", file=os.sys.stderr)
        return schemas

    async def _run_tool(self, call: dict[str, Any], iteration: int):
        """Execute one call and yield its events. Never raises."""
        import time as _t

        name = call.get("name") or "unknown"
        call_id = call.get("id") or "call_0"
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {"__raw__": str(arguments)}

        yield ToolCallStarted(
            call_id=call_id, name=name, arguments=arguments, iteration=iteration
        )

        tool = self._tools_by_name.get(name)
        started = _t.perf_counter()

        if tool is None:
            known = ", ".join(sorted(self._tools_by_name)) or "없음"
            message = f"알 수 없는 도구: {name}. 사용 가능한 도구: {known}"
            yield ToolCallFinished(
                call_id=call_id, name=name, ok=False, error=message,
                elapsed_ms=0.0, iteration=iteration,
            )
            yield call_id, message, False
            return

        try:
            output = await asyncio.wait_for(
                tool.execute(**arguments), timeout=self.tool_timeout
            )
        except asyncio.TimeoutError:
            message = (
                f"도구 시간 초과 ({self.tool_timeout:.0f}초). "
                f"`{name}` 호출이 중단되었습니다."
            )
            yield ToolCallFinished(
                call_id=call_id, name=name, ok=False, error=message,
                elapsed_ms=(_t.perf_counter() - started) * 1000.0, iteration=iteration,
            )
            yield call_id, message, False
            return
        except TypeError as exc:
            # Almost always an argument the model invented. Naming the schema
            # turns this from a dead end into a retry.
            message = (
                f"인자 오류: {exc}\n"
                f"스키마를 다시 확인하세요: {json.dumps(tool.schema(), ensure_ascii=False)[:600]}"
            )
            yield ToolCallFinished(
                call_id=call_id, name=name, ok=False, error=message,
                elapsed_ms=(_t.perf_counter() - started) * 1000.0, iteration=iteration,
            )
            yield call_id, message, False
            return
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            yield ToolCallFinished(
                call_id=call_id, name=name, ok=False, error=message,
                elapsed_ms=(_t.perf_counter() - started) * 1000.0, iteration=iteration,
            )
            yield call_id, message, False
            return

        text = output if isinstance(output, str) else str(output)
        elapsed_ms = (_t.perf_counter() - started) * 1000.0
        yield ToolCallFinished(
            call_id=call_id, name=name, ok=True,
            summary=text.splitlines()[0][:120] if text else "",
            elapsed_ms=elapsed_ms, iteration=iteration,
        )
        yield call_id, text, True

    # -- history ------------------------------------------------------------

    def _system_prompt(self, user_input: str) -> str:
        """Base prompt plus whatever the learning layer wants to add.

        The injector's failure is invisible by design: it returns the base
        prompt untouched, and there is nothing for the user to do about it
        either way.
        """
        base = self.system_prompt or DEFAULT_SYSTEM_PROMPT
        if self.injector is None:
            return base
        try:
            return self.injector.prompt(base, user_input).prompt
        except Exception:
            return base

    def initial_messages(self, user_input: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        prompt = self._system_prompt(user_input)
        if prompt:
            messages.append({"role": "system", "content": prompt})
        messages.append({"role": "user", "content": user_input})
        return messages

    def history_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Approximate tokens already occupied by the message list.

        Counts content only. Per-message protocol overhead (~3-4 tokens) is
        owned by `context/assembly.py`, where the budget is actually enforced.
        """
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                try:
                    total += int(self.tokenizer.count_tokens(content))
                except Exception:
                    from wx.context.assembly import estimate_tokens

                    total += estimate_tokens(content)
        return total

    # -- capture helpers ----------------------------------------------------

    def _capture_turn_start(self, user_input: str) -> None:
        if self.capture is None:
            return
        try:
            self.capture.begin_turn()
            self.capture.set_user_text(user_input)
        except Exception:
            pass

    def _capture_assistant(self, text: str) -> None:
        if self.capture is None:
            return
        try:
            self.capture.record_assistant(text)
        except Exception:
            pass

    def _capture_tool(self, name: str, arguments: Any, result: str, ok: bool) -> None:
        if self.capture is None:
            return
        try:
            if ok:
                self.capture.record_tool_result(name=name, arguments=arguments, result=result)
            else:
                self.capture.record_tool_error(name=name, arguments=arguments, error=result)
        except Exception:
            pass

    # -- turn ---------------------------------------------------------------

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        """Execute one turn and yield events as they happen.

        Always terminates with `TurnDone` or `Error`. Never raises for an
        expected failure — a dropped connection is an event the UI renders,
        not a traceback it has to survive.
        """
        self._capture_turn_start(user_input)
        messages = self.initial_messages(user_input)
        self._history = messages

        started = time.perf_counter()
        schemas = self.tool_schemas() if self.has_tools else None

        iteration = 0
        total_chunks = 0
        total_tool_calls = 0
        usage: Usage | None = None
        stop_reason: str | None = None
        interrupted = False
        all_text: list[str] = []

        _show_reasoning = os.environ.get("WX_DEBUG") == "1" or os.environ.get("WX_SHOW_REASONING") == "1"

        while iteration < self.max_iterations:
            iteration += 1
            turn_text: list[str] = []
            pending: dict[int, dict[str, Any]] = {}

            try:
                stream = self.client.stream(
                    messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    tools=schemas,
                    **({"tool_choice": "auto"} if schemas else {}),
                )

                async for ev in stream:
                    if isinstance(ev, TextChunk):
                        turn_text.append(ev.text)
                        total_chunks += 1
                        yield TextDelta(ev.text)
                    elif isinstance(ev, ReasoningChunk):
                        if _show_reasoning:
                            yield Notice(f"[reasoning] {ev.text}", level="info")
                    elif isinstance(ev, ToolCallDelta):
                        # Reassemble by index, never by arrival order.
                        slot = pending.setdefault(
                            ev.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if ev.call_id:
                            slot["id"] = ev.call_id
                        if ev.name:
                            slot["name"] = ev.name
                        if ev.arguments:
                            slot["arguments"] += ev.arguments
                    elif isinstance(ev, UsageChunk):
                        usage = ev.usage
                        yield usage
                    elif isinstance(ev, DoneChunk):
                        stop_reason = ev.stop_reason
                    elif isinstance(ev, StreamInterrupted):
                        interrupted = True
                        yield StreamInterrupted(
                            partial_text=ev.partial_text,
                            message=ev.message,
                            retryable=ev.retryable,
                        )
                        break

            except StreamError as exc:
                yield Error(message=str(exc), kind=exc.kind, retryable=exc.retryable)
                yield TurnDone(
                    text="".join(all_text),
                    usage=usage,
                    stop_reason=stop_reason,
                    elapsed_s=time.perf_counter() - started,
                    chunks=total_chunks,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                )
                self._finish_capture()
                return

            except (OSError, ValueError) as exc:
                yield Error(message=f"{type(exc).__name__}: {exc}", kind="internal")
                yield TurnDone(
                    text="".join(all_text),
                    usage=usage,
                    stop_reason=stop_reason,
                    elapsed_s=time.perf_counter() - started,
                    chunks=total_chunks,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                )
                self._finish_capture()
                return

            text = "".join(turn_text)
            all_text.append(text)

            # No tools requested: the model is done talking.
            if not pending:
                if iteration > 1:
                    self._capture_assistant(text)
                break

            # -- tool phase ------------------------------------------------
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": pending[i]["id"] or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": pending[i]["name"] or "unknown",
                            "arguments": pending[i]["arguments"],
                        },
                    }
                    for i in sorted(pending)
                ],
            }
            messages.append(assistant_message)

            for index in sorted(pending):
                call = pending[index]
                raw_args = call["arguments"] or ""
                try:
                    parsed = json.loads(raw_args) if raw_args.strip() else {}
                except (json.JSONDecodeError, TypeError):
                    parsed = {"__raw__": raw_args}
                if not isinstance(parsed, dict):
                    parsed = {"value": parsed}

                call_obj = {
                    "id": call["id"] or f"call_{index}",
                    "name": call["name"] or "unknown",
                    "arguments": parsed,
                }

                result_text, ok = "", True
                async for outcome in self._run_tool(call_obj, iteration):
                    if isinstance(outcome, tuple):
                        _, result_text, ok = outcome
                    else:
                        yield outcome

                total_tool_calls += 1
                clipped = _clip(result_text)
                self._capture_tool(call_obj["name"], parsed, result_text, ok)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_obj["id"],
                        "content": clipped or ("(빈 결과)" if ok else ""),
                    }
                )

            if interrupted:
                break

        if interrupted:
            stop_reason = "interrupted"
            yield TurnDone(
                text="".join(all_text),
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=total_chunks,
                iterations=iteration,
                tool_calls=total_tool_calls,
            )
        else:
            if iteration >= self.max_iterations and total_tool_calls:
                yield Notice(
                    f"반복 한도 {self.max_iterations}회에 도달했습니다. "
                    f"결과를 정리하고 멈춥니다.",
                    level="info",
                )
            final_text = "".join(all_text)
            self._capture_assistant(final_text)
            yield TurnDone(
                text=final_text,
                usage=usage,
                stop_reason=stop_reason,
                elapsed_s=time.perf_counter() - started,
                chunks=total_chunks,
                iterations=iteration,
                tool_calls=total_tool_calls,
            )
        self._finish_capture()

    def _finish_capture(self) -> None:
        if self.capture is None:
            return
        try:
            self.capture.end_turn()
        except Exception:
            pass