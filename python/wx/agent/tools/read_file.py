"""WUXICODE — Read file tool."""

from __future__ import annotations

import os
from typing import Any

from wx.agent.tools.base import Tool


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

        if not os.path.exists(abs_path):
            return f"오류: 파일 '{path}'을 찾을 수 없습니다."

        if not os.path.isfile(abs_path):
            return f"오류: '{path}'은 파일이 아닙니다."

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try binary detection
            try:
                with open(abs_path, "rb") as f:
                    content = f.read()
                return f"이진 파일 (크기: {len(content)} 바이트)"
            except Exception as e:
                return f"오류: 파일 읽기 실패 - {e}"
        except Exception as e:
            return f"오류: {e}"

        if not content:
            return "(빈 파일)"

        return content