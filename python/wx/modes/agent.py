"""WUXICODE — Agent mode.

Agent mode with full tool support.
"""

from __future__ import annotations

import os
import json
from collections.abc import AsyncIterator
from typing import Any

from wx.agent.tools import get_all_tools
from wx.llm.openai_client import (
    DoneChunk,
    ReasoningChunk,
    StreamInterrupted,
    TextChunk,
    ToolCallDelta,
    UsageChunk,
)
from wx.modes.base import Mode


def _debug(msg: str) -> None:
    """Print debug message if WX_DEBUG=1."""
    if os.environ.get("WX_DEBUG") == "1":
        print(f"[DEBUG] {msg}", file=__import__("sys").stderr)


def parse_json_robust(raw: str) -> dict:
    """
    Parse JSON that may have concatenated objects.
    Returns the first valid JSON object found.
    """
    raw = raw.strip()
    if not raw:
        return {}

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to extract first valid JSON object using raw_decode
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(raw)
        return obj
    except json.JSONDecodeError:
        pass

    # Fallback: try to extract JSON between braces
    try:
        # Find first { and matching }
        start = raw.find('{')
        if start >= 0:
            depth = 0
            for i, ch in enumerate(raw[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start:i+1]
                        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        pass

    return {}


# System prompt for agent mode - Korean only
AGENT_SYSTEM_PROMPT = """당신은 WX, 사용자의 로컬 코드베이스에서 요청을 실제로 실행하는 코딩 에이전트입니다.

## 절대 툴 호출 규칙
- 사용자가 파일을 만들거나, 읽거나, 수정하거나, 삭제하거나, 명령을 실행해 달라고 하면 즉시 해당 툴을 호출하세요.
- "만들겠습니다", "읽어드리겠습니다" 같은 말만 하고 툴을 호출하지 않으면 실패입니다.
- 툴 호출 없이 작업 결과를 상상하거나 완료했다고 말하지 마세요.
- 툴 이름이나 스키마를 답변에서 언급하는 것으로 실행을 대신하지 마세요.
- 툴 결과를 확인한 뒤에만 실제 수행 결과를 답하세요.

## 구체적 예시
사용자: "test.txt 만들어줘"
호출: write_file(path="test.txt", content="...")

사용자: "README.md 읽어줘"
호출: read_file(path="README.md")

사용자: "파일 목록 보여줘"
호출: bash(command="dir")

사용자: "현재 폴더야"
호출: bash(command="cd")

## 사용 가능한 도구
- read_file: 파일 읽기
- write_file: 파일 쓰기/생성
- edit_file: 파일 부분 수정
- bash: 셸 명령 실행 (워크스페이스 루트에서)
- git_status: Git 상태 확인
- git_diff: Git 변경사항 확인
- git_log: Git 커밋 로그

## 작업 규칙
- 항상 한국어로 응답하고 불필요한 서문은 생략하세요.
- 파일 경로는 워크스페이스 루트 기준 상대 경로를 사용하세요.
- 파일 작업, 쉘 명령, Git 작업은 반드시 도구로 수행하세요.
- 한 번에 하나의 도구만 호출하세요.
- 도구 호출이 실패하면 결과를 사실대로 알리고 같은 호출을 반복하지 마세요.
- bash는 워크스페이스 루트에서 실행합니다.
"""


class AgentMode(Mode):
    """Agent mode with full tool support."""

    name = "agent"
    icon = "🤖"
    label = "에이전트 모드"
    max_iterations = 25

    def __init__(self, router, messages: list[dict[str, Any]], workspace: str | None = None):
        self.router = router
        self.messages = messages
        workspace_root = workspace or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.set_workspace(workspace_root)
        self._failed_tool_calls: set[tuple[str, str]] = set()  # Track failed (tool_name, args_json)

    def set_workspace(self, workspace: str) -> None:
        """Set the workspace and rebuild tools bound to its root."""
        self.workspace = os.path.abspath(os.path.expanduser(workspace))
        self._tools = {
            tool.name: tool for tool in get_all_tools(workspace_root=self.workspace)
        }
        if hasattr(self, "_failed_tool_calls"):
            self._failed_tool_calls.clear()

    @property
    def available_tools(self) -> list[str]:
        """Return list of available tool names."""
        return list(self._tools.keys())

    def _get_tool_schemas(self) -> list[dict]:
        """Get OpenAI-compatible tool schemas for all tools."""
        return [tool.schema() for tool in self._tools.values()]

    @staticmethod
    def _is_action_request(text: str) -> bool:
        """Return whether a request asks the agent to perform an action."""
        keywords = (
            "만들", "생성", "써", "작성", "읽", "보여", "실행", "수정", "편집",
            "삭제", "옮", "복사", "현재 폴더", "파일 목록", "create", "write",
            "read", "edit", "delete", "run", "exec",
        )
        lowered = text.lower()
        return any(keyword in lowered for keyword in keywords)

    async def run(self, user_input: str) -> AsyncIterator[str]:
        """Run one bounded tool-calling turn and stream its visible output."""
        _debug(f"AgentMode.run() called with input: {user_input!r}")
        if not self.router:
            yield "오류: 라우터가 초기화되지 않았습니다."
            return

        self.messages.append({"role": "user", "content": user_input})
        system_prompt = f"{AGENT_SYSTEM_PROMPT}\n\n현재 워크스페이스: {self.workspace}"
        messages = [{"role": "system", "content": system_prompt}] + self.messages
        tool_schemas = self._get_tool_schemas()
        action_request = self._is_action_request(user_input)
        retry_prompt_used = False
        final_response = ""

        for iteration in range(self.max_iterations):
            _debug(f"Agent iteration {iteration + 1}/{self.max_iterations}")
            full_response = ""
            tool_calls: dict[int, dict[str, str]] = {}
            tool_choice = "required" if action_request else "auto"
            stream_failed = False

            try:
                async for ev in self.router.stream(
                    messages, tools=tool_schemas, tool_choice=tool_choice
                ):
                    if isinstance(ev, ToolCallDelta):
                        call = tool_calls.setdefault(
                            ev.index,
                            {
                                "id": ev.call_id or f"call_{ev.index}",
                                "name": "",
                                "arguments": "",
                            },
                        )
                        if ev.call_id:
                            call["id"] = ev.call_id
                        if ev.name:
                            call["name"] = ev.name
                        call["arguments"] += ev.arguments or ""
                    elif isinstance(ev, TextChunk):
                        full_response += ev.text
                        yield ev.text
                    elif isinstance(ev, ReasoningChunk):
                        _debug(f"Reasoning: {ev.text[:50]}...")
                    elif isinstance(ev, UsageChunk):
                        _debug(f"Usage: {ev.usage}")
                    elif isinstance(ev, DoneChunk):
                        _debug(f"Done: stop_reason={ev.stop_reason}")
                    elif isinstance(ev, StreamInterrupted):
                        _debug(f"Stream interrupted: {ev}")
                        yield f"\n[스트림 중단: {ev}]"
                        # The router moves on to the next model in the chain
                        # and keeps emitting ToolCallDelta on the same index.
                        # Those arguments concatenate onto the half-received
                        # ones from the dead model, and parse_json_robust /
                        # raw_decode accepts the result — that is how a
                        # truncated file lands on disk. Abandon the turn
                        # instead of running a call whose arguments never
                        # finished arriving.
                        stream_failed = True
                        if tool_calls:
                            names = ", ".join(
                                c["name"] or "<unnamed>" for c in tool_calls.values()
                            )
                            yield (
                                f"\n[중단됨: 미완성 도구 호출 {names} 폐기 — 실행하지 않았습니다]"
                            )
                        tool_calls.clear()
                        break
            except Exception as exc:
                _debug(f"Exception in stream: {type(exc).__name__}: {exc}")
                yield f"\n[오류: {exc}]"
                stream_failed = True

            if stream_failed:
                final_response = full_response
                break

            if not tool_calls:
                if action_request and not retry_prompt_used:
                    _debug("Action request returned no tool call; retrying once")
                    retry_prompt_used = True
                    messages.append({"role": "assistant", "content": full_response or None})
                    messages.append({
                        "role": "user",
                        "content": "지금 즉시 필요한 툴을 호출하세요. 말로만 답하지 마세요.",
                    })
                    continue
                final_response = full_response
                break

            messages.append({
                "role": "assistant",
                "content": full_response or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                    for call in tool_calls.values()
                ],
            })

            for call in tool_calls.values():
                name = call["name"]
                try:
                    args = parse_json_robust(call["arguments"])
                    normalized_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    args = {}
                    normalized_args = call["arguments"]
                call_key = (name, normalized_args)

                if call_key in self._failed_tool_calls:
                    result = "오류: 이전에 실패한 동일 도구 호출은 반복하지 않았습니다."
                else:
                    try:
                        tool = self._tools.get(name)
                        if tool is None:
                            raise ValueError(f"사용할 수 없는 도구: {name}")
                        result = await tool.execute(**args)
                        if result.startswith("오류:"):
                            self._failed_tool_calls.add(call_key)
                    except Exception as exc:
                        _debug(f"Tool execution error: {exc}")
                        result = f"오류: {exc}"
                        self._failed_tool_calls.add(call_key)

                _debug(f"Tool result: {result[:100]}...")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })

                display_path = args.get("path", "")
                if name == "read_file":
                    yield f"\n[파일 읽기] {display_path}\n{result}"
                elif name == "write_file":
                    yield f"\n[파일 쓰기] {display_path} — {result}"
                elif name == "edit_file":
                    yield f"\n[파일 편집] {display_path} — {result}"
                elif name == "bash":
                    yield f"\n[명령 실행] {result}"
                else:
                    yield f"\n[{name}] {result}"

            action_request = False
        else:
            yield "\n[최대 도구 호출 횟수에 도달했습니다.]"

        if final_response:
            self.messages.append({"role": "assistant", "content": final_response})
