"""WUXICODE — Bash tool."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from typing import Any

from wx.agent.tools.base import Tool


# Commands that are considered dangerous
DANGEROUS_COMMANDS = {
    "rm", "rmdir", "del", "format", "fdisk", "mkfs",
    "dd", "shutdown", "reboot", "halt", "poweroff",
    "chmod", "chown", "chgrp", "sudo", "su", "passwd",
    "useradd", "userdel", "groupadd", "groupdel",
    "mount", "umount", "systemctl", "service",
    "iptables", "ufw", "firewall-cmd",
    "crontab", "at", "batch",
}

# Patterns that indicate dangerous commands
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r">\s*/dev/",
    r"<\s*/dev/",
    r"mkfs\.",
    r"fdisk",
    r":\(\)\{\s*:\|\:&\s*\}\s*;\s*:",  # fork bomb
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*sh",
]

# Windows equivalents for common Unix commands
WINDOWS_CMD_MAP = {
    "ls": "dir",
    "ls -la": "dir",
    "ls -l": "dir",
    "ls -a": "dir /a",
    "cat": "type",
    "cp": "copy",
    "mv": "move",
    "rm": "del",
    "mkdir": "mkdir",
    "rmdir": "rmdir",
    "pwd": "cd",
    "which": "where",
    "grep": "findstr",
    "find": "dir /s /b",
    "head": "more",
    "tail": "more",
}


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


class BashTool(Tool):
    """Execute shell commands."""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "셸 명령어를 실행합니다. 워크스페이스 루트에서 실행됩니다."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "셸 명령어를 실행합니다. 워크스페이스 루트에서 실행됩니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "실행할 명령어"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "타임아웃(초), 기본 60초",
                            "default": 60
                        }
                    },
                    "required": ["command"],
                    "additionalProperties": False
                }
            }
        }

    def _translate_command(self, command: str) -> str:
        """Translate Unix commands to Windows equivalents if on Windows."""
        if os.name != "nt":
            return command
        
        # Split into command and args
        parts = shlex.split(command)
        if not parts:
            return command
        
        base_cmd = parts[0].lower()
        # Check if we have a Windows equivalent
        if base_cmd in WINDOWS_CMD_MAP:
            win_cmd = WINDOWS_CMD_MAP[base_cmd]
            # On Windows, dir doesn't support -la flags, strip Unix flags
            # Also strip trailing slashes from paths
            win_args = []
            for arg in parts[1:]:
                if not arg.startswith('-'):
                    # Strip trailing slashes/backslashes from paths
                    arg = arg.rstrip('/\\')
                    if arg:  # Only add non-empty args
                        win_args.append(arg)
            return win_cmd + (" " + " ".join(win_args) if win_args else "")
        
        return command

    async def execute(self, **kwargs) -> str:
        command = kwargs.get("command")
        timeout = kwargs.get("timeout", 60)

        if not command:
            return "오류: command 매개변수가 필요합니다."

        # Translate command for Windows
        command = self._translate_command(command)

        # Check for dangerous commands
        cmd_parts = shlex.split(command)
        if not cmd_parts:
            return "오류: 빈 명령어입니다."

        base_cmd = cmd_parts[0].lower()
        if base_cmd in DANGEROUS_COMMANDS:
            return f"오류: 위험한 명령어 '{base_cmd}'는 실행할 수 없습니다."

        cmd_str = " ".join(cmd_parts)
        import re
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_str, re.IGNORECASE):
                return f"오류: 위험한 패턴이 감지되었습니다: {pattern}"

        # On Windows, create_subprocess_shell already wraps with cmd.exe /c
        # Don't add "cmd /c" prefix to avoid double-wrapping
        pass
        import sys
        print(f"[DEBUG] Executing command: {command!r} (len={len(command)})", file=sys.stderr)
        print(f"[DEBUG] Command chars: {[c for c in command]}", file=sys.stderr)

        # Execute command in workspace root
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
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"오류: 타임아웃 ({timeout}초 초과)"

            stdout = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr = stderr.decode("utf-8", errors="replace") if stderr else ""

            output = ""
            if stdout:
                output += f"[stdout]\n{stdout}"
            if stderr:
                if output:
                    output += "\n"
                output += f"[stderr]\n{stderr}"
            if not output:
                output = "(출력 없음)"

            if proc.returncode != 0:
                return f"종료 코드: {proc.returncode}\n{output}"
            return output

        except asyncio.TimeoutError:
            return f"오류: 타임아웃 ({timeout}초 초과)"
        except Exception as e:
            return f"오류: 실행 실패 - {e}"