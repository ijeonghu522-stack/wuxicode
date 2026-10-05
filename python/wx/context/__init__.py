"""WUXICODE — context assembly.

Builds the system prompt for a turn: project rules, learned rules, and
retrieved files, held inside a token budget.

`AgentLoop.history_tokens` refers to this package as the place where the
budget is enforced, and that is where it lives — trimming after the prompt
exists is trimming too late.

    >>> from wx.context import AssemblyConfig, build_system_prompt
    >>> assembly = build_system_prompt("You are WX.", root=".")
    >>> assembly.tokens
    9
"""

from __future__ import annotations

from wx.context.assembly import (
    Assembly,
    AssemblyConfig,
    build_system_prompt,
    estimate_tokens,
    load_project_rules,
)

__all__ = [
    "Assembly",
    "AssemblyConfig",
    "build_system_prompt",
    "estimate_tokens",
    "load_project_rules",
]