"""WUXICODE — Write file tool."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from typing import Any

from wx.agent.tools.base import Tool


class WriteFileTool(Tool):
    """Write content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "파일에 내용을 씁니다. 파일이 없으면 생성하고, 있으면 덮어씁니다."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "파일에 내용을 씁니다. 파일이 없으면 생성하고, 있으면 덮어씁니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "쓸 파일의 상대 경로"
                        },
                        "content": {
                            "type": "string",
                            "description": "쓸 내용"
                        }
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path")
        content = kwargs.get("content", "")

        if not path:
            return "오류: path 매개변수가 필요합니다."
        if not isinstance(path, str):
            return "오류: path는 문자열이어야 합니다."
        if not isinstance(content, str):
            return "오류: content는 문자열이어야 합니다."

        validation_error = self._validate_content(path, content)
        if validation_error:
            answer = self._ask_retry()
            if answer in {"y", "yes"}:
                return (
                    f"오류: {validation_error} 사용자가 재시도를 선택했습니다. "
                    "내용을 보완해 write_file을 다시 호출하세요. 기존 파일은 유지했습니다."
                )
            if answer in {"n", "no"}:
                return f"오류: {validation_error} 사용자가 재시도를 취소했습니다. 기존 파일은 유지했습니다."
            return f"오류: {validation_error} 기존 파일은 변경하지 않았습니다."

        try:
            abs_path = self.validate_path(path)
        except ValueError as e:
            return f"오류: {e}"

        # Create parent directories if needed
        parent_dir = os.path.dirname(abs_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                return f"오류: 디렉토리 생성 실패 - {e}"

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent_dir or ".",
                prefix=f".{os.path.basename(abs_path)}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = f.name
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, abs_path)
            temp_path = None
        except Exception as e:
            return f"오류: 쓰기 실패 - {e}"
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        size = len(content.encode("utf-8"))
        return f"파일 '{path}' 작성 완료 ({size} 바이트)"

    @staticmethod
    def _validate_content(path: str, content: str) -> str | None:
        """Reject obvious truncated documents before replacing a real file."""
        if content.count("```") % 2:
            return "코드 블록이 닫히지 않았습니다. 완성된 내용을 다시 생성하세요."

        if os.path.splitext(path)[1].lower() in {".html", ".htm"}:
            has_document_start = bool(
                re.search(r"<!doctype\s+html\b|<html\b", content, re.IGNORECASE)
            )
            if has_document_start and not re.search(r"</html\s*>", content, re.IGNORECASE):
                return "HTML 문서의 </html> 닫는 태그가 없습니다."

            for tag in ("script", "style"):
                openings = len(re.findall(rf"<{tag}\b", content, re.IGNORECASE))
                closings = len(re.findall(rf"</{tag}\s*>", content, re.IGNORECASE))
                if openings > closings:
                    return f"<{tag}> 태그가 닫히지 않았습니다."

        return None

    @staticmethod
    def _ask_retry() -> str | None:
        if not sys.stdin.isatty():
            return None
        try:
            return input("[경고] 내용이 불완전합니다. 다시 시도할까요? (y/n) ").strip().lower()
        except (EOFError, OSError, KeyboardInterrupt):
            return None
