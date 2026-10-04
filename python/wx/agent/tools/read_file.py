"""WUXICODE — Read file tool.

The Phase 1B change is that this tool no longer re-reads a file it has already
read. A `read` result is keyed on five axes — tool, schema version, arguments,
(HEAD sha, index mtime), and the target's (mtime, size) — so an unchanged file
is never re-read, and a file edited one millisecond ago is never served stale.
See ``cpp/core/cache/tool_cache.hpp`` for the key and ``wx.agent.cache`` for the
process-wide instance.

Raw bytes are what gets cached. The binary/empty/unicode decisions below run on
whatever comes back, cached or not, so changing one of those messages does not
invalidate every entry ever stored.
"""

from __future__ import annotations

import os
from typing import Any

from wx.agent.cache import cache_available, descriptor, get_cache
from wx.agent.tools.base import Tool

# Bump when this tool's OUTPUT FORMAT changes. The cache keys on it, so a bump
# retires old results instead of serving last version's shape under this
# version's name.
READ_SCHEMA_VERSION = 1


class ReadFileTool(Tool):
    """Read a file's contents."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "파일 내용을 읽습니다. 경로는 워크스페이스 루트 기준 상대 경로여야 합니다."

    def is_read_only(self) -> bool:
        return True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "파일 내용을 읽습니다. 경로는 워크스페이스 루트 기준 상대 경로여야 합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "읽을 파일의 상대 경로"
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path")
        if not path:
            return "오류: path 매개변수가 필요합니다."

        try:
            abs_path = self.validate_path(path)
        except ValueError as e:
            return f"오류: {e}"

        # Errors are never cached. "Missing" is a state that changes, and a
        # cached one would have the agent insist a file it just wrote is absent.
        if not os.path.exists(abs_path):
            return f"오류: 파일 '{path}'을 찾을 수 없습니다."

        if not os.path.isfile(abs_path):
            return f"오류: '{path}'은 파일이 아닙니다."

        data: bytes | None = None
        d = None

        if cache_available():
            cache = get_cache(self.workspace_root)
            d = descriptor(
                self.name,
                self.workspace_root,
                path=abs_path,
                args={},  # `path` is axis 5; see wx.agent.cache.descriptor
                schema_version=READ_SCHEMA_VERSION,
            )
            hit = cache.get(d)
            if hit is not None:
                data = hit

        if data is None:
            try:
                with open(abs_path, "rb") as f:
                    data = f.read()
            except OSError as e:
                return f"오류: 파일 읽기 실패 - {e}"

            if d is not None:
                # False here only means "too large to cache". That is not an
                # error: the bytes in hand are still correct.
                cache.put(d, data)

        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"이진 파일 (크기: {len(data)} 바이트)"

        if not content:
            return "(빈 파일)"

        return content
