"""WUXICODE — Chat mode.

Standard conversational mode with message history.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from wx.llm.openai_client import (
    DoneChunk,
    ReasoningChunk,
    StreamInterrupted,
    TextChunk,
    UsageChunk,
)
from wx.modes.base import Mode
from wx.llm.router import ModelRouter


def _debug(msg: str) -> None:
    """Print debug message if WX_DEBUG=1."""
    if os.environ.get("WX_DEBUG") == "1":
        print(f"[DEBUG] {msg}", file=__import__("sys").stderr)


class ChatMode(Mode):
    """Standard chat mode - conversational with message history."""

    name = "chat"
    icon = "💬"
    label = "채팅 모드"

    def __init__(self, router: ModelRouter, messages: list[dict[str, Any]]):
        super().__init__(router, messages)

    async def run(self, user_input: str) -> AsyncIterator[str]:
        """Process user input and yield response chunks.

        Args:
            user_input: User's input string.

        Yields:
            String chunks of the assistant's response.
        """
        _debug(f"ChatMode.run() called with input: {user_input!r}")

        if not self.router:
            _debug("ERROR: router is None")
            yield "오류: 라우터가 초기화되지 않았습니다."
            return

        _debug(f"router present: {type(self.router)}")
        _debug(f"messages count: {len(self.messages)}")

        self.messages.append({"role": "user", "content": user_input})

        full_response = ""
        chunk_count = 0
        try:
            _debug("Starting router.stream()")
            async for ev in self.router.stream(self.messages):
                _debug(f"Received event type: {type(ev).__name__}")
                if isinstance(ev, TextChunk):
                    full_response += ev.text
                    chunk_count += 1
                    yield ev.text
                elif isinstance(ev, ReasoningChunk):
                    # Reasoning chunks are internal, don't yield to user
                    _debug(f"Reasoning: {ev.text[:50]}...")
                elif isinstance(ev, UsageChunk):
                    _debug(f"Usage: {ev.usage}")
                elif isinstance(ev, DoneChunk):
                    _debug(f"Done: stop_reason={ev.stop_reason}")
                elif isinstance(ev, StreamInterrupted):
                    # This is the openai_client *exception*, not the
                    # wx.agent.events dataclass — it carries its text in `args`.
                    _debug(f"Stream interrupted: {ev}")
                    yield f"\n[스트림 중단: {ev}]"
                    break
        except Exception as e:
            _debug(f"Exception in stream: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            yield f"\n[오류: {e}]"

        _debug(f"Stream ended, total text chunks: {chunk_count}")
        _debug(f"Final response length: {len(full_response)}")
        self.messages.append({"role": "assistant", "content": full_response})