"""WUXICODE — Edit file tool."""

from __future__ import annotations

from typing import Any

from wx.agent.tools.base import Tool


class EditFileTool(Tool):
    """Edit a file by replacing old text with new text."""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "파일의 특정 텍스트를 새로운 텍스트로 교체합니다."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "파일의 특정 텍스트를 새로운 텍스트로 교체합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "편집할 파일의 상대 경로"
                        },
                        "old_text": {
                            "type": "string",
                            "description": "대체할 기존 텍스트 (정확히 일치해야 함)"
                        },
                        "new_text": {
                            "type": "string",
                            "description": "새로운 텍스트"
                        }
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path")
        old_text = kwargs.get("old_text")
        new_text = kwargs.get("new_text")

        if not path:
            return "오류: path 매개변수가 필요합니다."
        if old_text is None:
            return "오류: old_text 매개변수가 필요합니다."
        if new_text is None:
            return "오류: new_text 매개변수가 필요합니다."

        if old_text == new_text:
            return "오류: old_text와 new_text가 동일합니다."

        try:
            abs_path = self.validate_path(path)
        except ValueError as e:
            return f"오류: {e}"

        if not os.path.exists(abs_path):
            return f"오류: 파일 '{path}'을 찾을 수 없습니다."

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            return "오류: 이진 파일은 편집할 수 없습니다."
        except Exception as e:
            return f"오류: 파일 읽기 실패 - {e}"

        if old_text not in content:
            # Show context around expected location
            return f"오류: '{old_text[:50]}...' 텍스트를 파일에서 찾을 수 없습니다."

        new_content = content.replace(old_text, new_text)
        if new_content == content:
            return "오류: 교체 후 내용이 동일합니다."

        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            return f"오류: 쓰기 실패 - {e}"

        return f"파일 '{path}' 편집 완료 (1개 위치 교체됨)"