"""WUXICODE — OpenAI-compatible streaming client (OpenRouter default).

Two layers, deliberately separated so the framing half is testable with no
network and no API key:

    iter_sse_frames(chunks)      bytes -> SseFrame        (pure, offline-testable)
    OpenAIClient.stream(...)     HTTP  -> StreamEvent     (network, retries)

`iter_sse_frames` hands every chunk straight to the native `SseParser`. Nothing
in Python re-splits lines, re-buffers, or looks for "\n\n" — that logic exists
in exactly one place, in C++, where it is a byte-level state machine.

`[DONE]` is interpreted HERE, not in the parser. The parser does not know what
an OpenAI payload is; that boundary is frozen in docs/adr/0001-sse-boundary.md.
When `[DONE]` arrives, the stream loop returns immediately and whatever the
server sends afterwards is ignored — trailers, blank keepalives, connection
teardown noise.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

import httpx

from wx.agent.events import Usage

__all__ = [
    "SseFrame",
    "TextChunk",
    "ReasoningChunk",
    "ToolCallDelta",
    "UsageChunk",
    "DoneChunk",
    "StreamEvent",
    "StreamError",
    "StreamInterrupted",
    "iter_sse_frames",
    "OpenAIClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DONE_SENTINEL",
]

# Space Bunny defaults (opencode zen route, no key required)
DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "space-bunny-free"
DONE_SENTINEL = "[DONE]"

# Fallback chain for 504/502/503/connection failures
FALLBACK_CHAIN: list[str] = []

# Streaming timeout: read=None disables idle timeout (first byte wait)
# connect=30, write=30, pool=30 for connection management
STREAM_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=None,  # No idle timeout — critical for slow free-model streams
    write=30.0,
    pool=30.0,
)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class StreamError(RuntimeError):
    """Transport, protocol or provider failure.

    `retryable` reflects whether the same request could succeed later. The
    caller uses it to decide between backoff, a different model slot, or
    giving up.
    """

    def __init__(self, message: str, *, kind: str = "unknown", retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable

    @property
    def message(self) -> str:
        """Human-readable message.

        An exception keeps this in `args[0]`. Consumers written against the
        event API (`wx.agent.events.StreamInterrupted`) reach for `.message`, so
        expose it here rather than making every call site unpack `args`.
        """
        return str(self.args[0]) if self.args else ""


class StreamInterrupted(StreamError):
    """Stream was cut off mid-response (upstream timeout, connection reset).

    This is distinct from a hard error — partial output was delivered.
    The caller should surface the partial text and allow the session to continue.
    """

    def __init__(self, message: str, partial_text: str = "", *, retryable: bool = True) -> None:
        super().__init__(message, kind="interrupted", retryable=retryable)
        self.partial_text = partial_text


# ---------------------------------------------------------------------------
# framing layer — no network, no key, fully testable
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str
    data: str
    id: str
    retry: str
    has_retry: bool


def _frame_from_dict(d: dict) -> SseFrame:
    return SseFrame(
        event=d.get("event", "message") or "message",
        data=d.get("data", "") or "",
        id=d.get("id", "") or "",
        retry=d.get("retry", "") or "",
        has_retry=bool(d.get("has_retry", False)),
    )


async def iter_sse_frames(
    chunks: AsyncIterator[bytes] | Iterable[bytes],
    *,
    parser: Any = None,
) -> AsyncIterator[SseFrame]:
    """Turn a byte stream into complete SSE frames.

    Accepts either an async iterator (httpx) or a plain iterable, so tests can
    drive it with a hand-written chunk list including deliberately hostile
    splits. Every split of a valid stream must produce identical output — that
    is exit criterion 3 and 4 for 1A.
    """
    if parser is None:
        from wx import require_core

        parser = require_core().SseParser()

    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:  # type: ignore[union-attr]
            for d in parser.feed(chunk):
                yield _frame_from_dict(d)
    else:
        for chunk in chunks:  # type: ignore[union-attr]
            for d in parser.feed(chunk):
                yield _frame_from_dict(d)

    # A stream cut off mid-frame still carries a complete field; flush it.
    parser.finish()
    for d in parser.take():
        yield _frame_from_dict(d)


# ---------------------------------------------------------------------------
# normalized stream events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningChunk:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """One fragment of an assembling tool call.

    Providers stream tool calls as ordered fragments keyed by `index`. The
    consumer reassembles by index — never by arrival order — because
    interleaving is normal and reordering would execute a call with another
    call's arguments.
    """

    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class UsageChunk:
    usage: Usage


@dataclass(frozen=True, slots=True)
class DoneChunk:
    stop_reason: str | None = None


StreamEvent: TypeAlias = TextChunk | ReasoningChunk | ToolCallDelta | UsageChunk | DoneChunk


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class OpenAIClient:
    """Streaming client for any OpenAI-compatible endpoint (OpenRouter default).

    Phase 1 wires only this provider. The WX AI and W_X ZEN slots land in
    `llm/router.py` at Phase 5; this class stays as one implementation behind
    that interface, not as a special case.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        # Priority: explicit arg > WX_LLM_BASE_URL env > DEFAULT_BASE_URL
        self.base_url = (
            base_url
            or os.environ.get("WX_LLM_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        # Priority: explicit arg > WX_LLM_MODEL env > DEFAULT_MODEL
        self.model = model or os.environ.get("WX_LLM_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_retries = max(0, max_retries)
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ---------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=STREAM_TIMEOUT,
                follow_redirects=True,
            )
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OpenAIClient:
        self._http()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- request -----------------------------------------------------------

    def build_payload(
        self,
        messages: list[dict[str, Any]],
        *,
        stream_options: bool = True,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if stream_options:
            # Without this the provider withholds usage on streamed responses
            # and the budget ledger has nothing to account against.
            payload["stream_options"] = {"include_usage": True}
        if tools is not None:
            payload["tools"] = tools
            # Prevent parallel tool calls to avoid JSON concatenation issues
            payload["parallel_tool_calls"] = False
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            else:
                payload["tool_choice"] = "required"
        for key, value in params.items():
            if value is not None:
                payload[key] = value
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "HTTP-Referer": "https://wuxicode.dev",
            "X-Title": "WUXICODE",
        }
        # httpx raises LocalProtocolError on an empty header value, so a
        # keyless endpoint must not receive the header at all rather than an
        # empty one.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # -- streaming ---------------------------------------------------------

    async def stream(
        self,
        messages: list[dict[str, Any]],
        **params: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Yield normalized stream events.

        Retries, but only before the first event reaches the caller. Once
        output is visible, a retry would duplicate text the user has already
        read; a clean failure is better than a silent one. Transport errors and
        429/5xx are retryable; 4xx other than 429 is not, because the request
        itself is wrong.

        If the stream is interrupted mid-response (504, connection reset),
        yields StreamInterrupted with partial text so the caller can surface it.
        """
        http = self._http()
        url = f"{self.base_url}/chat/completions"
        payload = self.build_payload(messages, **params)
        required_choice_fallback = False
        requested_tool_choice = payload.get("tool_choice")

        attempt = 0
        while True:
            emitted = False
            saw_tool_call = False
            partial_text = ""
            stop_reason: str | None = None
            try:
                async with http.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code >= 400:
                        raise await self._http_error(resp)

                    async for frame in iter_sse_frames(resp.aiter_bytes()):
                        if frame.data == DONE_SENTINEL:
                            # Everything after the sentinel is ignored.
                            yield DoneChunk(stop_reason=stop_reason)
                            return

                        for ev in _translate(frame):
                            if isinstance(ev, _Stop):
                                stop_reason = ev.stop_reason
                                continue
                            if isinstance(ev, TextChunk):
                                partial_text += ev.text
                            if isinstance(ev, ToolCallDelta):
                                saw_tool_call = True
                            emitted = True
                            yield ev

                # Stream ended without a sentinel. For a text-only turn that is still a
                # completed turn. For a turn that was mid-tool-call it is not —
                # the arguments arrived short, and reporting DoneChunk would
                # hand a truncated payload to the tool as if it were complete.
                if saw_tool_call:
                    raise StreamInterrupted(
                        "stream ended mid tool-call (no [DONE] sentinel)",
                        partial_text=partial_text,
                        retryable=True,
                    )
                yield DoneChunk(stop_reason=stop_reason)
                return

            except StreamError as exc:
                if (
                    requested_tool_choice == "required"
                    and not required_choice_fallback
                    and not emitted
                    and _is_unsupported_required_tool_choice(exc)
                ):
                    payload["tool_choice"] = "auto"
                    required_choice_fallback = True
                    continue
                if emitted or not exc.retryable or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(_backoff(attempt))
                attempt += 1
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # If we already emitted text, surface partial result
                if emitted:
                    raise StreamInterrupted(
                        f"stream interrupted: {exc!r}",
                        partial_text=partial_text,
                        retryable=True,
                    ) from exc
                if attempt >= self.max_retries:
                    raise StreamError(
                        f"transport failure: {exc!r}", kind="transport", retryable=True
                    ) from exc
                await asyncio.sleep(_backoff(attempt))
                attempt += 1

    async def _http_error(self, resp: httpx.Response) -> StreamError:
        body = b""
        try:
            body = await resp.aread()
        except httpx.HTTPError:
            pass
        detail = _extract_error_detail(body)
        retryable = resp.status_code == 429 or resp.status_code >= 500
        kind = {
            401: "auth",
            403: "permission",
            404: "not_found",
            413: "too_large",
            422: "bad_request",
            429: "rate_limited",
        }.get(resp.status_code, "http")
        return StreamError(
            f"HTTP {resp.status_code} from {resp.request.url.host}: {detail}",
            kind=kind,
            retryable=retryable,
        )


@dataclass(frozen=True, slots=True)
class _Stop:
    """Internal marker: provider signalled a finish reason, keep reading."""

    stop_reason: str | None


def _extract_error_detail(body: bytes) -> str:
    if not body:
        return "<empty body>"
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body.decode("utf-8", "replace")[:500]
    err = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str):
            return msg[:500]
    if isinstance(err, str):
        return err[:500]
    return json.dumps(obj)[:500]


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter, capped.

    Full jitter rather than a fixed exponential: a fleet of clients that all
    retry in lockstep is exactly what turns a 429 into an outage.
    """
    ceiling = min(8.0, 0.5 * (2**attempt))
    return random.uniform(0.0, ceiling)


def _is_unsupported_required_tool_choice(exc: StreamError) -> bool:
    """Identify providers that reject tool_choice='required'."""
    message = str(exc).lower().replace("_", " ")
    mentions_tool_choice = "tool choice" in message or "tool_choice" in message
    rejects_required = any(
        marker in message
        for marker in (
            "not supported",
            "unsupported",
            "does not support",
            "not available",
            "invalid value",
        )
    )
    return mentions_tool_choice and rejects_required


def _translate(frame: SseFrame) -> list[StreamEvent | _Stop]:
    """One SSE frame -> zero or more normalized events.

    The parser hands over bytes; everything payload-shaped happens here.
    """
    if not frame.data.strip():
        return []

    # WX_DEBUG=1: dump raw SSE frame
    if os.environ.get("WX_DEBUG") == "1":
        print(f"[DEBUG] SSE frame: {frame.data[:500]}", file=sys.stderr)

    try:
        obj = json.loads(frame.data)
    except ValueError as exc:
        # A malformed payload is a real fault. Dropping it silently would
        # corrupt the turn while looking like it worked.
        raise StreamError(
            f"provider sent a data frame that is not JSON: {frame.data[:300]!r}",
            kind="protocol",
            retryable=False,
        ) from exc

    if not isinstance(obj, dict):
        return []

    out: list[StreamEvent | _Stop] = []

    usage = obj.get("usage")
    if isinstance(usage, dict) and usage:
        out.append(UsageChunk(_parse_usage(usage)))

    for choice in obj.get("choices") or []:
        if not isinstance(choice, dict):
            continue

        if choice.get("finish_reason"):
            out.append(_Stop(stop_reason=choice.get("finish_reason")))

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue

        content = delta.get("content")
        if isinstance(content, str) and content:
            out.append(TextChunk(content))

        # Providers disagree on the reasoning field name. Accept both rather
        # than silently dropping chain-of-thought for one vendor.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            out.append(ReasoningChunk(reasoning))

        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            fn = fn if isinstance(fn, dict) else {}
            index = tc.get("index")
            out.append(
                ToolCallDelta(
                    index=int(index) if isinstance(index, int) else 0,
                    call_id=tc.get("id"),
                    name=fn.get("name"),
                    arguments=fn.get("arguments") or "",
                )
            )

    return out


def _parse_usage(raw: dict) -> Usage:
    def as_int(key: str) -> int:
        value = raw.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    prompt = as_int("prompt_tokens")
    completion = as_int("completion_tokens")
    total = as_int("total_tokens") or (prompt + completion)

    cached = 0
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict):
        value = details.get("cached_tokens")
        if isinstance(value, (int, float)):
            cached = int(value)

    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=cached,
    )


# Re-exported so callers can name the mode in a status line without importing
# two modules.
TokenAccuracy = Literal["exact", "heuristic"]
