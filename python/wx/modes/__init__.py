"""WUXICODE — Modes package."""

from __future__ import annotations

from wx.modes.base import Mode, ModeError, UnknownModeError
from wx.modes.chat import ChatMode
from wx.modes.agent import AgentMode
from wx.modes.registry import ModeRegistry, get_mode_registry

__all__ = [
    "Mode",
    "ModeError",
    "UnknownModeError",
    "ChatMode",
    "AgentMode",
    "ModeRegistry",
    "get_mode_registry",
]