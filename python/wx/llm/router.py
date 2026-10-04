"""WUXICODE — Model router with automatic fallback chain.

When a free-model stream fails with 504/502/503 or connection reset,
the router automatically retries with the next model in the chain.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from wx.llm.openai_client import (
    DoneChunk,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    FALLBACK_CHAIN,
    OpenAIClient,
    StreamError,
    StreamEvent,
    StreamInterrupted,
    ToolCallDelta,
)

__all__ = ["ModelRouter", "RouterConfig", "load_router_config"]

_OPENROUTER_MODEL_IDS: set[str] | None = None
_OPENROUTER_MODELS_CHECKED = False


def _fetch_openrouter_model_ids(timeout: float = 5.0) -> set[str] | None:
    """Fetch available model IDs once from OpenRouter's public catalog."""
    global _OPENROUTER_MODEL_IDS, _OPENROUTER_MODELS_CHECKED
    if _OPENROUTER_MODELS_CHECKED:
        return _OPENROUTER_MODEL_IDS

    _OPENROUTER_MODELS_CHECKED = True
    request = Request(
        f"{DEFAULT_BASE_URL}/models",
        headers={"Accept": "application/json", "User-Agent": "WUXICODE"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            catalog = json.loads(response.read().decode("utf-8"))
        _OPENROUTER_MODEL_IDS = {
            item["id"]
            for item in catalog.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        print(f"[경고] OpenRouter 모델 목록을 확인하지 못했습니다: {exc}", file=sys.stderr)
        _OPENROUTER_MODEL_IDS = None
    return _OPENROUTER_MODEL_IDS


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Router configuration loaded from config.json + env."""
    primary_model: str
    fallback_chain: list[str]
    base_url: str
    api_key: str
    timeout: float
    connect_timeout: float
    max_retries: int
    provider: str = "spacebunny"


def load_router_config() -> RouterConfig:
    """Load router configuration from config.json with env overrides."""
    from wx.config import load_config

    cfg = load_config()

    # Build fallback chain: configured values, then current package defaults.
    env_fallback = os.environ.get("WX_LLM_FALLBACK_CHAIN")
    if env_fallback:
        try:
            import json

            fallback_chain = json.loads(env_fallback)
        except json.JSONDecodeError:
            fallback_chain = FALLBACK_CHAIN
    else:
        # Use config's fallback_chain if explicitly set (including empty list), else default
        cfg_fallback = getattr(cfg, "fallback_chain", None)
        fallback_chain = cfg_fallback if cfg_fallback is not None else FALLBACK_CHAIN

    # Live catalog validation is only meaningful when the caller explicitly opted
    # into OpenRouter. DEFAULT_BASE_URL is now Groq, so a base_url comparison
    # would fire this block on every Groq config and strip the whole chain.
    if cfg.provider == "openrouter":
        from wx.config import DEFAULT_FALLBACK_CHAIN

        legacy_defaults = set(FALLBACK_CHAIN)
        if fallback_chain and all(model in legacy_defaults for model in fallback_chain):
            fallback_chain = list(DEFAULT_FALLBACK_CHAIN)

        available_models = _fetch_openrouter_model_ids()
        if available_models is not None:
            configured_models = [cfg.effective_model, *fallback_chain]
            missing = [model for model in configured_models if model not in available_models]
            for model in missing:
                print(f"[경고] OpenRouter /models에 없는 모델 ID: {model}", file=sys.stderr)

            if missing:
                fallback_chain = [
                    model for model in fallback_chain if model in available_models
                ]
                for model in DEFAULT_FALLBACK_CHAIN:
                    if (
                        model in available_models
                        and model != cfg.effective_model
                        and model not in fallback_chain
                    ):
                        fallback_chain.append(model)
                if not fallback_chain:
                    print("[경고] 사용 가능한 OpenRouter 폴백 모델이 없습니다.", file=sys.stderr)

    return RouterConfig(
        primary_model=cfg.effective_model,
        fallback_chain=fallback_chain,
        base_url=cfg.effective_base_url,
        api_key=cfg.api_key or "",
        timeout=cfg.timeout,
        connect_timeout=cfg.connect_timeout,
        max_retries=cfg.max_retries,
        provider=cfg.provider,
    )


class ModelRouter:
    """Routes requests across a fallback chain of models.

    On 504/502/503/connection errors mid-stream, switches to the next model.
    Max 3 attempts total (primary + 2 fallbacks).
    """

    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self._attempt = 0
        self._current_model = config.primary_model
        self._tried_models: list[str] = []

    @property
    def current_model(self) -> str:
        return self._current_model

    @property
    def models_tried(self) -> list[str]:
        return list(self._tried_models)

    def _next_model(self) -> str | None:
        """Get next model in fallback chain."""
        all_models = [self.config.primary_model] + self.config.fallback_chain
        for model in all_models:
            if model not in self._tried_models:
                return model
        return None

    def _extract_error_kind(self, exc: Exception) -> str:
        """Extract error kind from exception for display."""
        if isinstance(exc, StreamError):
            return exc.kind
        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg:
            return "rate_limit"
        if "504" in msg or "gateway timeout" in msg:
            return "timeout"
        if "503" in msg or "unavailable" in msg:
            return "unavailable"
        if "502" in msg or "bad gateway" in msg:
            return "bad_gateway"
        if "401" in msg or "unauthorized" in msg or "auth" in msg:
            return "auth"
        if "403" in msg or "forbidden" in msg or "permission" in msg:
            return "permission"
        if "connection" in msg or "connect" in msg:
            return "connection"
        return "error"

    async def stream(
        self,
        messages: list[dict[str, Any]],
        **params: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream with automatic fallback on retryable failures.

        Yields StreamEvent from the active model. On StreamInterrupted
        (mid-stream cutoff), yields the partial result then retries next model.
        """
        # Reset state for each new stream (each turn)
        self._attempt = 0
        self._tried_models.clear()
        self._current_model = self.config.primary_model

        partial_text = ""
        last_error_kind = "unknown"

        while self._attempt < 3:
            model = self._next_model()
            if model is None:
                break

            self._current_model = model
            self._tried_models.append(model)
            self._attempt += 1

            client = OpenAIClient(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                model=model,
                timeout=self.config.timeout,
                connect_timeout=self.config.connect_timeout,
                max_retries=self.config.max_retries,
            )

            emitted_any = False
            pending_tool_calls: list[ToolCallDelta] = []
            retry_model = False

            try:
                async with client:
                    async for ev in client.stream(messages, **params):
                        if isinstance(ev, StreamInterrupted):
                            # Stream cut off mid-response — capture partial
                            partial_text = ev.partial_text
                            emitted_any = True
                            yield ev
                            retry_model = True
                            break  # Exit this model's stream, try fallback

                        if isinstance(ev, ToolCallDelta):
                            # Do not expose partial arguments before the model
                            # confirms that its tool-call response is complete.
                            pending_tool_calls.append(ev)
                            emitted_any = True
                            continue

                        if isinstance(ev, DoneChunk):
                            if ev.stop_reason not in {"stop", "tool_calls", "function_call"}:
                                reason = ev.stop_reason or "누락"
                                yield StreamInterrupted(
                                    f"응답이 중간에 끊겼습니다 (finish_reason={reason})",
                                    partial_text=partial_text,
                                    retryable=True,
                                )
                                retry_model = True
                                break
                            for tool_call in pending_tool_calls:
                                yield tool_call
                            yield ev
                            continue

                        if isinstance(ev, StreamEvent):
                            if hasattr(ev, "text"):
                                partial_text += ev.text
                            emitted_any = True
                            yield ev

                if retry_model:
                    await asyncio.sleep(1.0)
                    continue

                # If we got here without interruption, stream completed.
                return

            except StreamError as exc:
                last_error_kind = self._extract_error_kind(exc)
                # If we emitted text before the error, surface it as interrupted
                if emitted_any:
                    yield StreamInterrupted(
                        f"model {model} failed: {exc}",
                        partial_text=partial_text,
                        retryable=True,
                    )
                else:
                    # No output yet — show fallback reason
                    yield StreamInterrupted(
                        f"[fallback] {model}: {last_error_kind}",
                        partial_text="",
                        retryable=True,
                    )
                # Rate limit 완화: 1초 대기 후 다음 모델
                await asyncio.sleep(1.0)
                continue

            except Exception as exc:
                last_error_kind = self._extract_error_kind(exc)
                if emitted_any:
                    yield StreamInterrupted(
                        f"model {model} error: {exc}",
                        partial_text=partial_text,
                        retryable=True,
                    )
                else:
                    yield StreamInterrupted(
                        f"[fallback] {model}: {last_error_kind}",
                        partial_text="",
                        retryable=True,
                    )
                await asyncio.sleep(1.0)
                continue

        # Exhausted all models
        if self._attempt >= 3:
            if partial_text:
                yield StreamInterrupted(
                    f"모든 폴백 실패 (마지막 에러: {last_error_kind}). 부분 출력 전달.",
                    partial_text=partial_text,
                    retryable=False,
                )
            else:
                raise StreamError(
                    f"모든 {self._attempt}개 모델 폴백 실패 (마지막 에러: {last_error_kind})",
                    kind="exhausted",
                    retryable=False,
                )
