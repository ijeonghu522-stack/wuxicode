"""WUXICODE — Provider Management TUI.

Interactive UI for managing provider API keys and models.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

from wx.auth.key_validator import add_provider, list_configured_providers, mask_key
from wx.auth.provider_store import get_provider_store
from wx.providers.registry import get_provider, PROVIDER_REGISTRY
from wx.ui import brand
from wx.ui.width import pad_to, str_width, truncate_to


@dataclass
class ProviderItem:
    """Provider item for TUI display."""
    name: str
    display_name: str
    has_key: bool
    enabled: bool
    model_count: int
    key_display: str
    base_url: str
    key_help_url: str
    requires_key: bool


class ProviderManager:
    """Provider management TUI."""

    def __init__(self, color: bool = True):
        self.color = color
        self._selected = 0
        self._mode = "list"  # "list", "input_key", "confirm_remove"
        self._input_buffer = ""
        self._current_provider = ""
        self._confirm_action = ""
        self._save_error = ""

    def _supports_ansi(self) -> bool:
        if not sys.stdout.isatty():
            return False
        if os.name != "nt":
            return True
        return (
            os.environ.get("WT_SESSION") is not None
            or os.environ.get("TERM_PROGRAM") is not None
            or os.environ.get("ANSICON") is not None
            or os.environ.get("ConEmuANSI") == "ON"
        )

    def _clear_screen(self) -> None:
        try:
            if not sys.stdout.isatty():
                return
            if os.name == "nt":
                if self._supports_ansi():
                    sys.stdout.write("\033[2J\033[H")
                else:
                    os.system("cls")
            else:
                sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        except Exception:
            pass

    def _render_provider_list(self, providers: list[ProviderItem]) -> str:
        width = 58

        if not self.color:
            def row(text: str) -> str:
                return "│" + pad_to(truncate_to(" " + text, width), width) + "│"

            lines = ["┌" + "─" * width + "┐"]
            lines.append(row("프로바이더 관리"))
            for i, p in enumerate(providers):
                marker = ">" if i == self._selected else " "
                status = "✅" if p.has_key else "⬜"
                enabled = "활성" if p.enabled else "비활성"
                key_info = f" {p.key_display}" if p.has_key else " [키 필요]"
                lines.append(row(f"{marker} {i+1}. {status} {p.display_name} ({enabled}){key_info}"))
            lines.append("├" + "─" * width + "┤")
            lines.append(row("↑↓ 이동  [Enter] 키 관리  [r] 새로고침  [Esc/q] 취소"))
            lines.append("└" + "─" * width + "┘")
            return "\n".join(lines)

        CYAN = brand.fg((0x06, 0xB6, 0xD4))
        GREEN = brand.fg((0x7C, 0xFF, 0x3B))
        YELLOW = brand.fg((0xFF, 0xB0, 0x20))
        RED = brand.fg((0xFF, 0x55, 0x55))
        DIM = brand.DIM
        RESET = brand.RESET

        top = f"{CYAN}╭{'─' * width}╮{RESET}"
        bottom = f"{CYAN}╰{'─' * width}╯{RESET}"

        def row(text: str) -> str:
            return f"  {CYAN}│{RESET}{pad_to(truncate_to(' ' + text, width), width)}{CYAN}│{RESET}"

        lines = [f"  {top}"]
        lines.append(row(f"{CYAN}프로바이더 관리{RESET}"))

        for i, p in enumerate(providers):
            is_sel = i == self._selected
            marker = f"{CYAN}►{RESET}" if is_sel else "  "

            status_icon = f"{GREEN}✓{RESET}" if p.has_key else f"{DIM}○{RESET}"
            enabled_str = f"{GREEN}활성{RESET}" if p.enabled else f"{DIM}비활성{RESET}"

            key_info = ""
            if p.has_key:
                key_info = f" {CYAN}{p.key_display}{RESET}"
            elif p.requires_key:
                key_info = f" {YELLOW}[키 필요]{RESET}"

            # Line 1: provider name + status
            name_line = f"  {marker} {status_icon} {p.display_name} ({enabled_str}){key_info}"
            # Line 2: base URL + model count
            detail = f"       {DIM}│ {p.model_count}개 모델  ·  {p.base_url}{RESET}"

            lines.append(row(name_line))
            lines.append(row(detail))

        lines.append(f"  {CYAN}├{'─' * width}┤{RESET}")
        lines.append(row(f"{brand.DIM}↑↓ 이동  Enter 키 관리  r 새로고침  Esc/q 취소{RESET}"))
        lines.append(f"  {bottom}")
        return "\n".join(lines)

    def _render_key_input(self, provider: ProviderItem) -> str:
        width = 54

        cursor = "_" if len(self._input_buffer) % 2 == 0 else " "

        if not self.color:
            def row(text: str) -> str:
                return "│" + pad_to(truncate_to(" " + text, width), width) + "│"

            lines = ["┌" + "─" * width + "┐"]
            lines.append(row(f"프로바이더: {provider.display_name}"))
            lines.append(row(f"발급: {provider.key_help_url}"))
            lines.append(row(""))
            lines.append(row(f"API 키: {self._input_buffer}{cursor}"))
            lines.append(row(""))
            if self._save_error:
                lines.append(row(f"오류: {self._save_error}"))
                lines.append(row(""))
            lines.append(row("[Enter] 저장·검증  [Esc] 취소"))
            lines.append("└" + "─" * width + "┘")
            return "\n".join(lines)

        CYAN = brand.fg((0x06, 0xB6, 0xD4))
        GREEN = brand.fg((0x7C, 0xFF, 0x3B))
        YELLOW = brand.fg((0xFF, 0xB0, 0x20))
        RED = brand.fg((0xFF, 0x55, 0x55))
        DIM = brand.DIM
        RESET = brand.RESET

        top = f"{CYAN}╭{'─' * width}╮{brand.RESET}"
        bottom = f"{CYAN}╰{'─' * width}╯{brand.RESET}"

        masked = "*" * len(self._input_buffer) if self._input_buffer else ""
        bar = "█" if len(self._input_buffer) % 2 == 0 else " "

        def row(text: str) -> str:
            return f"  {CYAN}│{brand.RESET}{pad_to(truncate_to(' ' + text, width), width)}{CYAN}│{brand.RESET}"

        lines = [f"  {top}"]
        lines.append(row(f"{CYAN}{provider.display_name} API 키 입력{RESET}"))
        lines.append(row(f"{DIM}발급: {provider.key_help_url}{RESET}"))
        lines.append(row(""))
        lines.append(row(f"  API 키: {masked}{bar}"))
        lines.append(row(""))
        if self._save_error:
            lines.append(row(f"{RED}오류: {self._save_error}{RESET}"))
            lines.append(row(""))
        lines.append(row(f"{DIM}[Enter] 저장·검증  [Esc] 취소{RESET}"))
        lines.append(f"  {bottom}")
        return "\n".join(lines)

    def _render_confirm(self, provider: ProviderItem, action: str) -> str:
        action_text = "해제" if action == "remove" else "삭제"
        width = 50

        if not self.color:
            def row(text: str) -> str:
                return "│" + pad_to(truncate_to(" " + text, width), width) + "│"

            lines = ["┌" + "─" * width + "┐"]
            lines.append(row(f"{provider.display_name} {action_text} 확인"))
            lines.append(row(f"정말로 {action_text}하시겠습니까?"))
            lines.append(row(f"저장된 키: {self._get_stored_key_display(provider)}"))
            lines.append(row(""))
            lines.append(row("[y] 예  [n] 아니오"))
            lines.append("└" + "─" * width + "┘")
            return "\n".join(lines)

        CYAN = brand.fg((0x06, 0xB6, 0xD4))
        RED = brand.fg((0xFF, 0x55, 0x55))
        DIM = brand.DIM
        RESET = brand.RESET

        top = f"{RED}╭{'─' * width}╮{brand.RESET}"
        bottom = f"{RED}╰{'─' * width}╯{brand.RESET}"

        def row(text: str) -> str:
            return f"  {RED}│{brand.RESET}{pad_to(truncate_to(' ' + text, width), width)}{RED}│{brand.RESET}"

        lines = [f"  {top}"]
        lines.append(row(f"{RED}{provider.display_name} {action_text} 확인{RESET}"))
        lines.append(row(f"정말로 {action_text}하시겠습니까?"))
        lines.append(row(f"{DIM}저장된 키: {self._get_stored_key_display(provider)}{RESET}"))
        lines.append(row(""))
        lines.append(row(f"{DIM}[y] 예  [n] 아니오{RESET}"))
        lines.append(f"  {bottom}")
        return "\n".join(lines)

    def _get_stored_key_display(self, provider: ProviderItem) -> str:
        store = get_provider_store()
        stored = store._data.get(provider.name)
        return mask_key(stored.api_key) if stored and stored.api_key else "(없음)"

    def get_providers_list(self) -> list[ProviderItem]:
        """Get list of providers for display."""
        providers = []
        for info in list_configured_providers():
            providers.append(ProviderItem(
                name=info["name"],
                display_name=info["display_name"],
                has_key=info["has_key"],
                enabled=info["enabled"],
                model_count=info["model_count"],
                key_display=info["key_display"],
                base_url=info["base_url"],
                key_help_url=info["key_help_url"],
                requires_key=info["requires_key"],
            ))
        return providers

    def _draw(self, providers: list[ProviderItem]) -> None:
        self._clear_screen()
        if self._mode == "list":
            sys.stdout.write(self._render_provider_list(providers) + "\n")
        elif self._mode == "input_key":
            provider = next((p for p in providers if p.name == self._current_provider), None)
            if provider:
                sys.stdout.write(self._render_key_input(provider) + "\n")
        elif self._mode == "confirm_remove":
            provider = next((p for p in providers if p.name == self._current_provider), None)
            if provider:
                sys.stdout.write(self._render_confirm(provider, self._confirm_action) + "\n")
        sys.stdout.flush()

    async def run(self) -> None:
        """Run the provider manager loop.

        Async all the way through. This TUI is driven from inside the
        agent's own event loop (cli.py awaits run_interactive under
        asyncio.run), so the key handlers that touch the network must
        await on the ambient loop -- calling asyncio.run() here would
        raise RuntimeError.
        """
        if not sys.stdin.isatty():
            print("Provider manager requires interactive terminal")
            return

        if os.name == "nt":
            await self._run_windows()
        else:
            await self._run_unix()

    async def _run_unix(self) -> None:
        import termios
        import tty

        old_settings = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setcbreak(sys.stdin.fileno())
            providers = self.get_providers_list()
            self._draw(providers)

            while True:
                ch = sys.stdin.read(1)
                if self._mode == "list":
                    await self._handle_list_input(ch, providers)
                elif self._mode == "input_key":
                    await self._handle_key_input(ch, providers)
                elif self._mode == "confirm_remove":
                    await self._handle_confirm_input(ch, providers)

                providers = self.get_providers_list()
                self._draw(providers)
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)

    async def _run_windows(self) -> None:
        import msvcrt

        while True:
            if not msvcrt.kbhit():
                import time
                time.sleep(0.01)
                continue

            providers = self.get_providers_list()
            ch = msvcrt.getwch()

            if self._mode == "list":
                await self._handle_list_input(ch, providers)
            elif self._mode == "input_key":
                await self._handle_key_input(ch, providers)
            elif self._mode == "confirm_remove":
                await self._handle_confirm_input(ch, providers)

            providers = self.get_providers_list()
            self._draw(providers)

    def _read_key(self) -> str:
        """Read one key from the tty, platform-appropriate.

        The Windows loop drives input through msvcrt.getwch(); the Unix
        loop runs in cbreak and reads sys.stdin. A multi-byte arrow key
        must be consumed from the same source that started it, otherwise
        the trailing byte is read from the wrong stream.
        """
        if os.name == "nt":
            import msvcrt
            return msvcrt.getwch()
        return sys.stdin.read(1)

    async def _handle_list_input(self, ch: str, providers: list[ProviderItem]) -> None:
        if not providers:
            return

        # Escape/arrow prefixes must be handled BEFORE the bare ESC/quit
        # test, otherwise \x1b[ A is read as "user pressed ESC" and the
        # navigation branch below is unreachable.
        if ch in ("\xe0", "\x00"):  # Windows special-key prefix
            ch2 = self._read_key()
            if ch2 == "H":  # Up
                self._selected = (self._selected - 1) % len(providers)
            elif ch2 == "P":  # Down
                self._selected = (self._selected + 1) % len(providers)
        elif ch == "\x1b":  # Unix escape sequence, or bare ESC to quit
            ch2 = self._read_key()
            if ch2 == "[":
                ch3 = self._read_key()
                if ch3 == "A":  # Up
                    self._selected = (self._selected - 1) % len(providers)
                elif ch3 == "B":  # Down
                    self._selected = (self._selected + 1) % len(providers)
            else:
                raise SystemExit(0)  # bare ESC
        elif ch in ("q", "Q"):  # quit
            raise SystemExit(0)
        elif ch in ("\r", "\n"):  # Enter
            if providers:
                self._current_provider = providers[self._selected].name
                if providers[self._selected].has_key:
                    self._mode = "confirm_remove"
                    self._confirm_action = "remove"
                else:
                    self._mode = "input_key"
                    self._input_buffer = ""
                    self._save_error = ""
        elif ch == "r" or ch == "R":  # Refresh
            await self._refresh_all()

    async def _handle_key_input(self, ch: str, providers: list[ProviderItem]) -> None:
        if ch in ("\x1b", "q", "Q"):  # ESC or q
            self._mode = "list"
            self._input_buffer = ""
            self._save_error = ""
        elif ch in ("\r", "\n"):  # Enter
            if self._input_buffer.strip():
                await self._save_key()
        elif ch in ("\x08", "\x7f"):  # Backspace/Delete
            self._input_buffer = self._input_buffer[:-1]
        elif len(ch) == 1 and ch.isprintable():
            self._input_buffer += ch

    async def _handle_confirm_input(self, ch: str, providers: list[ProviderItem]) -> None:
        if ch in ("y", "Y"):
            self._remove_provider()
        elif ch in ("n", "N", "\x1b", "q", "Q"):
            self._mode = "list"

    async def _refresh_all(self) -> None:
        """Refresh models for all enabled providers."""
        store = get_provider_store()
        for name, provider in store._data.items():
            if provider.enabled and provider.api_key:
                from wx.auth.key_validator import fetch_and_store_models
                await fetch_and_store_models(name, provider.api_key)

    async def _save_key(self) -> None:
        api_key = self._input_buffer.strip()
        if not api_key:
            return

        provider = get_provider(self._current_provider)
        if not provider:
            return

        # Validate and store
        success, error = await add_provider(self._current_provider, api_key)
        if success:
            self._mode = "list"
            self._input_buffer = ""
            self._save_error = ""
        else:
            # Keep the user in the input box and show why it failed.
            self._save_error = error or "키 검증 실패"

    def _remove_provider(self) -> None:
        provider_name = self._current_provider
        store = get_provider_store()
        store.remove_key(provider_name)
        self._mode = "list"
        self._confirm_action = ""


async def run_provider_manager() -> None:
    """Run provider manager (async entry point)."""
    manager = ProviderManager()
    await manager.run()