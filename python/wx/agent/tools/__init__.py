"""WUXICODE — Agent tools package."""

from __future__ import annotations

from wx.agent.tools.base import Tool, ToolCall, ToolResult

__all__ = [
    "Tool",
    "ToolCall", 
    "ToolResult",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "BashTool",
    "GitStatusTool",
    "GitDiffTool",
    "GitLogTool",
    "get_all_tools",
    "get_tool_by_name",
]

# Import concrete implementations
from wx.agent.tools.read_file import ReadFileTool
from wx.agent.tools.write_file import WriteFileTool
from wx.agent.tools.edit_file import EditFileTool
from wx.agent.tools.bash import BashTool
from wx.agent.tools.git_tools import GitStatusTool, GitDiffTool, GitLogTool

# Registry of all available tools
_TOOL_REGISTRY: dict[str, type] = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "edit_file": EditFileTool,
    "bash": BashTool,
    "git_status": GitStatusTool,
    "git_diff": GitDiffTool,
    "git_log": GitLogTool,
}


def get_all_tools(workspace_root: str | None = None) -> list:
    """Get list of all available tool instances."""
    return [cls(workspace_root=workspace_root) for cls in _TOOL_REGISTRY.values()]


def get_tool_by_name(name: str, workspace_root: str | None = None):
    """Get tool instance by name."""
    cls = _TOOL_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown tool: {name}")
    return cls(workspace_root=workspace_root)


def get_all_schemas() -> list[dict]:
    """Get OpenAI-compatible schemas for all tools."""
    return [tool().schema() for tool in get_all_tools()]