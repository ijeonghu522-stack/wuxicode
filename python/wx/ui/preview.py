"""WUXICODE — Preview system (stub).

Preview system for code/file preview. Currently a stub for EXE phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Preview:
    """Preview system for code/file content.

    Currently a stub. Full implementation planned for EXE phase with
    actual rendering (syntax highlighting, markdown, images, etc.).
    """

    content: str | None = None
    type: str | None = None  # "code", "markdown", "image", "diff", etc.
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def set(self, content: str, type_: str, language: str | None = None, **metadata: Any) -> None:
        """Set preview content.

        Args:
            content: The content to preview.
            type_: Content type ("code", "markdown", "image", "diff", etc.)
            language: Programming language for code highlighting.
            **metadata: Additional metadata (filename, line numbers, etc.)
        """
        self.content = content
        self.type = type_
        self.language = language
        self.metadata = metadata

    def clear(self) -> None:
        """Clear preview content."""
        self.content = None
        self.type = None
        self.language = None
        self.metadata.clear()

    def render(self) -> str:
        """Render preview as string (stub)."""
        if not self.content:
            return "미리보기: 없음"

        type_str = self.type or "unknown"
        lang_str = f" ({self.language})" if self.language else ""
        size = len(self.content)
        return f"[미리보기: {type_str}{lang_str}] {size} bytes"

    def is_empty(self) -> bool:
        return self.content is None

    def has_content(self) -> bool:
        return self.content is not None


# Global preview instance
_preview: "Preview | None" = None


def get_preview() -> "Preview":
    """Get global preview instance."""
    global _preview
    if _preview is None:
        _preview = Preview()
    return _preview


def set_preview(preview: "Preview") -> None:
    """Set custom preview instance (for testing)."""
    global _preview
    _preview = preview