"""WUXICODE — Model Selector UI.

Data-driven, renderer-agnostic model picker.
Supports TUI (terminal) now, GUI (EXE) later via renderer interface.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Protocol

from wx.ui import brand
from wx.ui.width import pad_to, truncate_to


# =============================================================================
# DATA — ModelPreset (pure data, no UI logic)
# =============================================================================


@dataclass(frozen=True, slots=True)
class ModelPreset:
    """Model preset configuration. Pure data, no UI logic."""

    name: str
    id: str
    provider: str
    desc: str
    icon: str = "⚡"
    tier: str = "free"           # "free" | "paid"
    base_url: str | None = None
    requires_key: str | None = None


DEFAULT_PRESETS: list[ModelPreset] = [
    ModelPreset(
        name="WX AI (기본)",
        id="qwen/qwen3.8-27b",
        provider="openrouter",
        desc="OpenRouter · 무료",
        icon="⚡",
        tier="free",
    ),
    ModelPreset(
        name="Llama 3.3 (Groq)",
        id="llama-3.3-70b-versatile",
        provider="custom",
        base_url="https://api.groq.com/openai/v1",
        requires_key="GROQ_API_KEY",
        desc="Groq · 무료 · 빠름",
        icon="🚀",
        tier="free",
    ),
    ModelPreset(
        name="DeepSeek V3",
        id="deepseek/deepseek-chat-v3:free",
        provider="openrouter",
        desc="OpenRouter · 무료",
        icon="🧠",
        tier="free",
    ),
    ModelPreset(
        name="GPT-4o mini",
        id="gpt-4o-mini",
        provider="openai",
        requires_key="OPENAI_API_KEY",
        desc="OpenAI · 유료",
        icon="🔑",
        tier="paid",
    ),
    ModelPreset(
        name="Claude Sonnet 4.5",
        id="claude-sonnet-4-5",
        provider="custom",
        base_url="https://api.anthropic.com/v1",
        requires_key="ANTHROPIC_API_KEY",
        desc="Anthropic · 유료",
        icon="🎭",
        tier="paid",
    ),
]


# =============================================================================
# RENDERER INTERFACE
# =============================================================================


class ModelSelectorRenderer(Protocol):
    """Renderer interface for model selector."""

    def render(self, presets: list[ModelPreset], active_id: str) -> str:
        """Render the selector UI as string."""

    def get_choice(self, presets: list[ModelPreset], active_id: str) -> int | None:
        """Run interactive loop. Return chosen index or None if cancelled."""


# =============================================================================
# TUI RENDERER (Terminal UI)
# =============================================================================


class TuiRenderer:
    """Terminal-based beautiful model selector."""

    def __init__(self, color: bool = True):
        self.color = color
        self._selected = 0
        self._box_height = 0

    def _supports_ansi(self) -> bool:
        """Check if terminal supports ANSI escape codes."""
        if not sys.stdout.isatty():
            return False
        if os.name != "nt":
            return True
        # Windows: check for ANSI support
        return (
            os.environ.get("WT_SESSION") is not None  # Windows Terminal
            or os.environ.get("TERM_PROGRAM") is not None  # VS Code, etc.
            or os.environ.get("ANSICON") is not None
            or os.environ.get("ConEmuANSI") == "ON"
        )

    def _clear_screen(self) -> None:
        """Clear the entire screen using platform-appropriate method."""
        try:
            if not sys.stdout.isatty():
                return
            if os.name == "nt":
                # Windows: try ANSI first, fallback to cls
                if self._supports_ansi():
                    sys.stdout.write("\033[2J\033[H")
                else:
                    os.system("cls")
            else:
                # Unix: ANSI escape sequence
                sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        except Exception:
            pass  # Clear failed, continue anyway

    def _has_key(self, preset: ModelPreset) -> bool:
        if not preset.requires_key:
            return True
        return bool(os.environ.get(preset.requires_key))

    def _render_item(self, preset: ModelPreset, idx: int, is_selected: bool, is_active: bool) -> str:
        if not self.color:
            marker = ">" if is_selected else " "
            status = ""
            if is_active:
                status = " [active]"
            elif not self._has_key(preset):
                status = " [키 필요]"
            return f"{marker} {idx + 1}. {preset.name} ({preset.id}) - {preset.desc}{status}"

        CYAN = brand.fg((0x06, 0xB6, 0xD4))
        GREEN = brand.fg((0x7C, 0xFF, 0x3B))
        ORANGE = brand.fg((0xFF, 0x6B, 0x5E))
        YELLOW = brand.fg((0xFF, 0xB0, 0x20))
        DIM = brand.DIM
        RESET = brand.RESET

        radio = f"{CYAN}●{RESET}" if is_selected else f"{DIM}○{RESET}"
        num = f"{CYAN}{idx + 1}.{RESET}" if is_selected else f"{DIM}{idx + 1}.{RESET}"
        name = f"{CYAN}{preset.name}{RESET}" if is_selected else preset.name
        id_info = f"{DIM}{preset.id}{RESET}"
        tier_tag = f" {GREEN}[무료]{RESET}" if preset.tier == "free" else f" {ORANGE}[유료]{RESET}"
        key_status = ""
        if not self._has_key(preset):
            key_status = f" {YELLOW}[키 필요]{RESET}"
        elif is_active:
            key_status = f" {CYAN}[active]{RESET}"

        return f"  {radio} {num} {name} {tier_tag}{key_status}\n      {DIM}│ {id_info}  ·  {preset.desc}{RESET}"

    def _render_box(self, presets: list[ModelPreset], active_id: str) -> str:
        width = 54

        if not self.color:
            def row(text: str) -> str:
                return "│" + pad_to(truncate_to(" " + text, width), width) + "│"

            lines = ["┌" + "─" * width + "┐"]
            lines.append(row("모델 선택"))
            for i, p in enumerate(presets):
                for line in self._render_item(p, i, i == self._selected, p.id == active_id).split("\n"):
                    lines.append(row(line))
            lines.append("├" + "─" * width + "┤")
            lines.append(row("↑↓ 이동  Enter 선택  Esc/q 취소  1-9 바로 선택"))
            lines.append("└" + "─" * width + "┘")
            return "\n".join(lines)

        CYAN = brand.fg((0x06, 0xB6, 0xD4))
        DIM = brand.DIM
        RESET = brand.RESET
        top = f"{CYAN}╭{'─' * width}╮{RESET}"
        bottom = f"{CYAN}╰{'─' * width}╯{RESET}"

        def row(text: str) -> str:
            return f"  {CYAN}│{RESET}{pad_to(truncate_to(' ' + text, width), width)}{CYAN}│{RESET}"

        lines = [f"  {top}"]
        lines.append(row(f"{CYAN}모델 선택{RESET}"))

        for i, p in enumerate(presets):
            item = self._render_item(p, i, i == self._selected, p.id == active_id)
            for line in item.split("\n"):
                lines.append(row(line))

        lines.append(f"  {CYAN}├{'─' * width}┤{RESET}")
        lines.append(row(f"{DIM}↑↓ 이동  Enter 선택  Esc/q 취소  1-9 바로 선택{RESET}"))
        lines.append(f"  {bottom}")
        return "\n".join(lines)

    def _draw(self, presets: list[ModelPreset], active_id: str) -> None:
        self._clear_screen()
        sys.stdout.write(self._render_box(presets, active_id) + "\n")
        sys.stdout.flush()

    def _clear(self) -> None:
        self._clear_screen()

    def get_choice(self, presets: list[ModelPreset], active_id: str) -> int | None:
        """Run interactive loop. Return chosen index or None if cancelled."""
        if not sys.stdin.isatty():
            return next((i for i, p in enumerate(presets) if p.id == active_id), 0)

        self._selected = next((i for i, p in enumerate(presets) if p.id == active_id), 0)
        self._draw(presets, active_id)

        if os.name == "nt":
            return self._run_windows(presets)
        return self._run_unix(presets)

    def _run_unix(self, presets: list[ModelPreset]) -> int | None:
        import termios
        import tty

        old_settings = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setcbreak(sys.stdin.fileno())
            while True:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":
                            self._selected = (self._selected - 1) % len(presets)
                            self._draw(presets, presets[self._selected].id)
                        elif ch3 == "B":
                            self._selected = (self._selected + 1) % len(presets)
                            self._draw(presets, presets[self._selected].id)
                elif ch in ("\r", "\n"):
                    self._clear()
                    return self._selected
                elif ch in ("q", "Q", "\x1b"):
                    self._clear()
                    return None
                elif ch.isdigit():
                    idx = int(ch) - 1
                    if 0 <= idx < len(presets):
                        self._selected = idx
                        self._clear()
                        return self._selected
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        return None

    def _run_windows(self, presets: list[ModelPreset]) -> int | None:
        import msvcrt

        while True:
            if not msvcrt.kbhit():
                import time
                time.sleep(0.01)
                continue
            ch = msvcrt.getwch()
            if ch == "\xe0":
                ch2 = msvcrt.getwch()
                if ch2 == "H":
                    self._selected = (self._selected - 1) % len(presets)
                    self._draw(presets, presets[self._selected].id)
                elif ch2 == "P":
                    self._selected = (self._selected + 1) % len(presets)
                    self._draw(presets, presets[self._selected].id)
            elif ch in ("\r", "\n"):
                self._clear()
                return self._selected
            elif ch in ("q", "Q", "\x1b"):
                self._clear()
                return None
            elif ch.isdigit():
                idx = int(ch) - 1
                if 0 <= idx < len(presets):
                    self._selected = idx
                    self._clear()
                    return self._selected
        return None


# =============================================================================
# GUI RENDERER (Placeholder for EXE)
# =============================================================================


class GuiRenderer:
    """GUI renderer placeholder for future EXE build."""

    def render(self, presets: list[ModelPreset], active_id: str) -> str:
        return ""

    def get_choice(self, presets: list[ModelPreset], active_id: str) -> int | None:
        return None


# =============================================================================
# FACTORY & HELPERS
# =============================================================================


def get_renderer(mode: str = "tui", color: bool = True) -> ModelSelectorRenderer:
    """Get renderer by mode."""
    if mode == "gui":
        return GuiRenderer()
    return TuiRenderer(color=color)


def load_presets_from_config(cfg) -> list[ModelPreset]:
    """Load model presets from config, fallback to defaults."""
    presets_data = getattr(cfg, "model_presets", None)
    if presets_data:
        presets = []
        for p in presets_data:
            presets.append(ModelPreset(
                name=p.get("name", p.get("id", "")),
                id=p.get("id", ""),
                provider=p.get("provider", "openrouter"),
                desc=p.get("desc", ""),
                icon=p.get("icon", "⚡"),
                tier=p.get("tier", "free"),
                base_url=p.get("base_url"),
                requires_key=p.get("requires_key"),
            ))
        return presets
    return DEFAULT_PRESETS


def save_presets_to_config(cfg, presets: list[ModelPreset]) -> None:
    """Save model presets to user config file."""
    from wx.config import get_user_config_path
    import json

    path = get_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config to preserve other fields
    existing = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    existing.update({
        "model_presets": [
            {
                "name": p.name,
                "id": p.id,
                "provider": p.provider,
                "desc": p.desc,
                "icon": p.icon,
                "tier": p.tier,
                "base_url": p.base_url,
                "requires_key": p.requires_key,
            }
            for p in presets
        ],
    })

    with path.open("w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def run_model_selector(
    presets: list[ModelPreset],
    active_id: str,
    mode: str = "tui",
    color: bool = True,
) -> ModelPreset | None:
    """Run model selector with given presets and mode."""
    renderer = get_renderer(mode, color)
    idx = renderer.get_choice(presets, active_id)
    if idx is not None and 0 <= idx < len(presets):
        return presets[idx]
    return None