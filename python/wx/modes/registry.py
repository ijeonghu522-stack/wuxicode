"""WUXICODE — Mode registry.

Registry for managing available modes and current mode state.
"""

from __future__ import annotations

from typing import Any

from wx.modes.base import ModeError, UnknownModeError


class ModeRegistry:
    """Registry for managing available modes."""

    def __init__(self):
        self._modes: dict[str, type] = {}
        self._instances: dict[str, Any] = {}
        self._current_name: str = "chat"

    def register(self, mode_class: type) -> None:
        """Register a mode class.

        Args:
            mode_class: Mode class to register (must have 'name' attribute).
        """
        if not hasattr(mode_class, "name"):
            raise ModeError(f"Mode class {mode_class} must have 'name' attribute")
        self._modes[mode_class.name] = mode_class

    def switch(self, name: str) -> Any:
        """Switch to a different mode.

        Args:
            name: Name of the mode to switch to.

        Returns:
            The mode instance for the new current mode.

        Raises:
            UnknownModeError: If the mode is not registered.
        """
        if name not in self._modes:
            raise UnknownModeError(f"Unknown mode: {name}. Available: {list(self._modes.keys())}")

        self._current_name = name
        return self.get_current()

    def get_current(self) -> Any:
        """Get the current mode instance, creating it if needed."""
        mode_class = self._modes.get(self._current_name)
        if not mode_class:
            raise ModeError(f"No mode registered for: {self._current_name}")

        if self._current_name not in self._instances:
            # Instance will be created by the caller (run_interactive)
            # This is just a fallback - the actual instance is passed externally
            raise ModeError(f"Mode instance not initialized for: {self._current_name}")

        return self._instances[self._current_name]

    def set_instance(self, name: str, instance: Any) -> None:
        """Set the instance for a mode (called during initialization)."""
        if name not in self._modes:
            raise UnknownModeError(f"Cannot set instance for unknown mode: {name}")
        self._instances[name] = instance

    @property
    def current_name(self) -> str:
        return self._current_name

    @property
    def available_modes(self) -> list[str]:
        return list(self._modes.keys())


# Global registry instance
_registry: ModeRegistry | None = None


def get_mode_registry() -> ModeRegistry:
    """Get the global mode registry instance."""
    global _registry
    if _registry is None:
        _registry = ModeRegistry()
    return _registry


def set_mode_registry(registry: ModeRegistry) -> None:
    """Set custom mode registry (for testing)."""
    global _registry
    _registry = registry