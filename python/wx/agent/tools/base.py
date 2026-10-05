"""WUXICODE — Agent tool base classes.

Abstract base class for all agent tools with OpenAI-compatible schema.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of a tool execution."""
    call_id: str
    name: str
    output: str
    error: str | None = None

    def is_error(self) -> bool:
        return self.error is not None


class Tool(ABC):
    """Abstract base class for all agent tools.

    Each tool must implement:
    - name: unique identifier
    - description: human-readable description for the LLM
    - schema(): OpenAI-compatible function schema
    - execute(): actual implementation
    """

    name: str
    description: str

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = workspace_root or os.getcwd()

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (e.g., 'read_file')."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM."""
        pass

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return OpenAI-compatible function schema.

        Example:
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file's contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to workspace"}
                    },
                    "required": ["path"]
                }
            }
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool with given arguments.

        Returns:
            String output (success message or error description).
        """
        pass

    def is_read_only(self) -> bool:
        """Whether this tool only reads data (no side effects)."""
        return False

    def validate_path(self, path: str) -> str:
        """Resolve and validate path is within workspace."""
        # Resolve to absolute path
        abs_path = os.path.abspath(os.path.join(self.workspace_root, path))
        workspace_abs = os.path.abspath(self.workspace_root)

        # Check if path is within workspace
        if not abs_path.startswith(workspace_abs + os.sep) and abs_path != workspace_abs:
            raise ValueError(f"Path '{path}' escapes workspace")

        return abs_path