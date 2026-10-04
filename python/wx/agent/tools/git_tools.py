"""WUXICODE — Git tools."""

from __future__ import annotations

import asyncio
from typing import Any

from wx.agent.tools.base import Tool


class GitBaseTool(Tool):
    """Base class for git tools."""

    async def _run_git(self, command: str, timeout: int = 30) -> str:
        """Run git command in workspace root.

        Must stay async. These tools execute inside the agent's own event loop,
        where asyncio.run() raises RuntimeError; awaiting the subprocess on the
        ambient loop is the only thing that works here.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self.workspace_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"오류: 타임아웃 ({timeout}초 초과)"

            stdout = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr = stderr.decode("utf-8", errors="replace") if stderr else ""
            if proc.returncode != 0:
                return f"오류 (종료 코드 {proc.returncode}): {stderr or stdout}"
            return stdout.strip() if stdout else "(출력 없음)"
        except Exception as e:
            return f"오류: {e}"


class GitStatusTool(GitBaseTool):
    """Show git status."""

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return "Git 저장소 상태를 확인합니다."

    def is_read_only(self) -> bool:
        return True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "Git 저장소 상태를 확인합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        return await self._run_git("git status")


class GitDiffTool(GitBaseTool):
    """Show git diff."""

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Git 변경 사항을 확인합니다."

    def is_read_only(self) -> bool:
        return True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": "Git 변경 사항을 확인합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "staged": {
                            "type": "boolean",
                            "description": "스테이징된 변경사항만 볼지 여부",
                            "default": False
                        }
                    },
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        staged = kwargs.get("staged", False)
        cmd = "git diff --cached" if staged else "git diff"
        return await self._run_git(cmd)


class GitLogTool(GitBaseTool):
    """Show git log."""

    @property
    def name(self) -> str:
        return "git_log"

    @property
    def description(self) -> str:
        return "Git 커밋 로그를 확인합니다."

    def is_read_only(self) -> bool:
        return True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": "Git 커밋 로그를 확인합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "표시할 커밋 수",
                            "default": 10
                        },
                        "oneline": {
                            "type": "boolean",
                            "description": "한 줄로 표시",
                            "default": True
                        }
                    },
                    "additionalProperties": False
                }
            }
        }

    async def execute(self, **kwargs) -> str:
        # `count` reaches here straight from the model. It lands in a shell
        # command string, so coerce it to a bounded int instead of trusting
        # whatever came back.
        try:
            count = int(kwargs.get("count", 10))
        except (TypeError, ValueError):
            count = 10
        count = max(1, min(200, count))

        oneline = kwargs.get("oneline", True)
        fmt = "--oneline" if oneline else ""
        cmd = f"git log {fmt} -n {count}"
        return await self._run_git(cmd)