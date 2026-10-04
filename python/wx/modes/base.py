"""WUXICODE — Mode base classes.

Abstract base class for modes and common interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from wx.agent.events import Event
from wx.llm.router import ModelRouter


class Mode(ABC):
    """Abstract base class for interaction modes.

    A mode defines how user input is processed and what tools are available.
    Each mode owns its message history and processing logic.
    """

    name: str
    icon: str
    label: str

    def __init__(self, router: ModelRouter, messages: list[dict[str, Any]]):
        self.router = router
        self.messages = messages

    @abstractmethod
    async def run(self, user_input: str) -> AsyncIterator[str]:
        """Process user input and yield response chunks.

        Args:
            user_input: Raw user input string.

        Yields:
            String chunks of the response.
        """
        ...

    def available_tools(self) -> list[str]:
        """Return list of available tool names for this mode."""
        return []

    def get_prompt_prefix(self) -> str:
        """Return the prompt prefix for this mode (e.g., '[CHAT] 💬')."""
        return f"[{self.name.upper()}] {self.icon}"

    def get_status_suffix(self) -> str:
        """Return status bar suffix for this mode."""
        return self.label


class ModeError(Exception):
    """Raised when a mode operation fails."""
    pass


class UnknownModeError(ModeError):
    """Raised when requesting an unknown mode."""
    pass