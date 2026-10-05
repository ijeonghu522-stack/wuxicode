"""WUXICODE — command line entry point.

`wuxicode "<prompt>"` streams a turn. `made by ujin` is pinned to the
bottom-right of the status line and re-rendered on every update, so it is
visible for the whole run rather than only in the banner.

Two behaviours worth stating:

  * Output is a stream, not a transcript. Text deltas are written as they
    arrive and coalesced at ~20 ms so that a fast provider does not turn into
    a write() syscall per token. Piping to a file gives byte-identical content
    to what was displayed, minus the status line.

  * A missing API key is not an error, it is a purchase screen. The tool exits
    2 with instructions and the contact address, because the most likely reason
    to run it without a key is that you have not bought one yet.

Interactive mode:
  wuxicode              → 대화형 진입
  wuxicode chat         → 대화형 진입
  wuxicode "hello"      → 일회성 (현재 유지)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any

from wx import __version__, require_core
from wx.agent.events import Error, Notice, StreamInterrupted, TextDelta, TurnDone
from wx.agent.loop import AgentLoop
from wx.config import load_config, provider_needs_key
from wx.llm.openai_client import StreamError
from wx.llm.router import ModelRouter, load_router_config
from wx.modes import ChatMode, AgentMode, get_mode_registry
from wx.ui import banner, brand
from wx.ui.model_selector import (
    load_presets_from_config,
    run_model_selector,
)
from wx.ui.provider_manager import run_provider_manager
from wx.ui.preview import get_preview

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_NO_KEY = 2
EXIT_ERROR = 1

FLUSH_INTERVAL_S = 0.02


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


class DeltaWriter:
    """Buffers deltas and flushes at most every `min_interval` seconds.

    Writing and flushing each delta turns a 200-token-per-second stream into
    200 syscalls per second. The visible result is identical; the latency
    overhead is not.
    """

    def __init__(self, stream=None, min_interval: float = FLUSH_INTERVAL_S) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._min_interval = min_interval
        self._buffer: list[str] = []
        self._last = 0.0

    def write(self, text: str) -> None:
        self._buffer.append(text)
        now = time.monotonic()
        if now - self._last >= self._min_interval:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        payload = "".join(self._buffer)
        self._buffer.clear()
        self._last = time.monotonic()
        self._stream.write(payload)
        self._stream.flush()

    def close(self) -> None:
        self.flush()


class StatusLine:
    """Status display — only shows final summary, never during streaming."""

    def __init__(self, stream=None, interactive: bool = False) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._enabled = brand.supports_color(self._stream)
        self._interactive = interactive
        self._drawn = False

    def update(self, left: str, right: str = "") -> None:
        # Don't update during streaming to avoid interleaving with response
        pass

    def clear(self) -> None:
        if not self._drawn or not self._enabled:
            return
        if self._interactive:
            self._stream.write("\n")
        else:
            self._stream.write("\r\x1b[K")
        self._stream.flush()
        self._drawn = False

    def show_final(self, left: str) -> None:
        """Show final status line after response completes."""
        if not self._enabled:
            print(left)
            return
        # No right footer (no "made by ujin")
        self._stream.write(left + "\n")
        self._stream.flush()
        self._drawn = True

    @staticmethod
    def _width() -> int:
        try:
            return max(40, min(shutil_width(), 120))
        except Exception:
            return 80

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        if limit <= 1:
            return ""
        return text if len(text) <= limit else text[: limit - 1] + brand.ellipsis()


def shutil_width() -> int:
    import shutil

    return shutil.get_terminal_size(fallback=(80, 24)).columns


def humanize_ms(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{seconds % 60:.0f}s"


def _humanize_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wuxicode",
        description=f"{brand.PRODUCT_NAME} — {brand.AI_BRAND}, a hybrid coding agent.",
        epilog=f"{brand.footer()}\n{brand.purchase_hint()}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="what you want done (omit for interactive)")
    parser.add_argument(
        "-m", "--model", default=None, help="model slot (overrides config)"
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible endpoint (overrides config)",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="request timeout, seconds")
    parser.add_argument(
        "--max-tokens", type=int, default=None, help="cap on completion tokens per turn"
    )
    parser.add_argument(
        "--temperature", type=float, default=None, help="sampling temperature"
    )
    parser.add_argument("--system-prompt", default=None, help="override the system prompt")
    parser.add_argument("--no-banner", action="store_true", help="suppress the first-run banner")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parser.add_argument(
        "--key", default=None, help="API key (overrides WX_LLM_API_KEY env var)"
    )
    parser.add_argument(
        "--provider",
        choices=["spacebunny", "openrouter", "openai", "groq", "custom"],
        default=None,
        help="LLM provider (overrides config)",
    )
    parser.add_argument(
        "--doctor", action="store_true", help="check the native core and exit"
    )
    parser.add_argument(
        "--learn",
        action="store_true",
        help="distil recent tool calls into learned rules, publish them, and prune",
    )
    parser.add_argument(
        "--learn-days",
        type=float,
        default=7.0,
        metavar="N",
        help="window for --learn (default: 7 days)",
    )
    parser.add_argument(
        "--version", action="version", version=f"{brand.PRODUCT_NAME} {__version__}"
    )
    return parser


# ---------------------------------------------------------------------------
# learn
# ---------------------------------------------------------------------------


def run_learn(days: float) -> int:
    """Run the nightly digest and print its report.

    This is the loop that makes the agent specific to a repository rather than
    generic: every failed `bash` call and every repeated successful one becomes
    a rule that gets injected into the next turn's system prompt. Cheap to run,
    so it is meant to be run daily rather than as a special occasion.
    """
    from wx.learn import run_digest

    try:
        report = run_digest(window_days=days)
    except Exception as exc:  # the layer is optional; never take the CLI down
        print(f"학습 리포트 생성 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(report.report_text)
    if report.notes:
        return EXIT_ERROR
    return EXIT_OK


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def run_doctor() -> int:
    color = not os.environ.get("NO_COLOR") and brand.supports_color(sys.stdout)
    print(banner.render(color=color))
    try:
        core = require_core()
    except Exception as exc:
        print(f"  native core   : FAILED\n{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"  python        : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  native core   : OK  version={core.version()} channel={core.channel()}")
    print(f"  product       : {core.product()}  license={core.license()}")
    print(f"  tokenizer     : {core.tokenizer_mode()}")
    print(f"  sse parser    : {core.SseParser.__name__} ready")
    print()
    print(f"  WUXICODE build version : {__version__}")
    if core.version() != __version__:
        print(
            f"  MISMATCH: extension reports {core.version()}, package reports "
            f"{__version__}. Rebuild with `pip install -e .`.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    cfg = load_config()
    key = _find_key(None, cfg)
    print(f"  api key       : {'found (' + key_source(key, cfg) + ')' if key else 'NOT SET'}")
    print(f"  provider      : {cfg.provider}")
    print(f"  model         : {cfg.effective_model}")
    print(f"  base_url      : {cfg.effective_base_url}")
    print()
    print(f"  {brand.footer()}")
    return EXIT_OK


def key_source(key: str, cfg) -> str:
    if os.environ.get("WX_LLM_API_KEY"):
        return "WX_LLM_API_KEY"
    if key:
        return "argument"
    if cfg.api_key:
        return "config"
    return "none"


def _find_key(explicit: str | None, cfg) -> str | None:
    return explicit or os.environ.get("WX_LLM_API_KEY") or cfg.api_key


# ---------------------------------------------------------------------------
# no-key screen
# ---------------------------------------------------------------------------


def print_no_key(args: argparse.Namespace) -> None:
    color = not args.no_color and brand.supports_color(sys.stdout)
    print()
    print(banner.render(color=color))
    print()
    print("  No API key configured. Set one and run again:")
    print()
    print("      set WX_LLM_API_KEY=sk-...        (PowerShell)")
    print("      export WX_LLM_API_KEY=sk-...     (bash)")
    print("      wuxicode --key sk-... \"hello\"")
    print()
    print(f"  {brand.purchase_hint()}")
    print()
    print(f"  {brand.footer()}")
    print()


# ---------------------------------------------------------------------------
# interactive mode
# ---------------------------------------------------------------------------


def _build_router_from_args(args: argparse.Namespace, key: str, cfg) -> ModelRouter:
    """Build router config with CLI overrides."""
    router_cfg = load_router_config()
    # Always use the explicit key from CLI/env
    router_cfg = type(router_cfg)(
        primary_model=router_cfg.primary_model,
        fallback_chain=router_cfg.fallback_chain,
        base_url=router_cfg.base_url,
        api_key=key,
        timeout=router_cfg.timeout,
        connect_timeout=router_cfg.connect_timeout,
        max_retries=router_cfg.max_retries,
    )
    if args.model:
        router_cfg = type(router_cfg)(
            primary_model=args.model,
            fallback_chain=router_cfg.fallback_chain,
            base_url=router_cfg.base_url,
            api_key=key,
            timeout=router_cfg.timeout,
            connect_timeout=router_cfg.connect_timeout,
            max_retries=router_cfg.max_retries,
        )
    if args.base_url:
        router_cfg = type(router_cfg)(
            primary_model=router_cfg.primary_model,
            fallback_chain=router_cfg.fallback_chain,
            base_url=args.base_url,
            api_key=key,
            timeout=router_cfg.timeout,
            connect_timeout=router_cfg.connect_timeout,
            max_retries=router_cfg.max_retries,
        )
    if args.provider:
        from wx.config import DEFAULT_PROVIDER, PROVIDER_DEFAULTS
        defaults = PROVIDER_DEFAULTS.get(args.provider, PROVIDER_DEFAULTS[DEFAULT_PROVIDER])
        router_cfg = type(router_cfg)(
            primary_model=args.model or defaults.get("model", router_cfg.primary_model),
            fallback_chain=[],  # Clear fallback for provider switch
            base_url=args.base_url or defaults.get("base_url", router_cfg.base_url),
            api_key=key,
            timeout=args.timeout,
            connect_timeout=10.0,
            max_retries=3,
        )
    return ModelRouter(router_cfg)


def _print_prompt(color: bool) -> None:
    """Print the interactive prompt."""
    if color:
        prompt = brand.fg(brand.GRADIENT_START) + "› " + brand.RESET
    else:
        prompt = "> "
    sys.stdout.write(prompt)
    sys.stdout.flush()


async def _handle_command(cmd: str, router: ModelRouter, loop: AgentLoop, messages: list, color: bool, cfg, agent_mode=None) -> tuple[bool, bool]:
    """Handle slash commands. Returns (continue_loop, handled)."""
    raw_cmd = cmd.strip()
    cmd = raw_cmd.lower()

    if cmd in ("/exit", "/quit", ":q"):
        return False, True

    if cmd == "/clear":
        messages.clear()
        # Re-add system prompt if available
        system_prompt = None
        if loop is not None and hasattr(loop, 'system_prompt'):
            system_prompt = loop.system_prompt
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[히스토리 초기화됨]{brand.RESET}")
        else:
            print("  [히스토리 초기화됨]")
        return True, True

    if cmd == "/providers":
        # Launch provider manager
        await run_provider_manager()
        return True, True

    # /mode commands - handled in main loop now, but kept for compatibility
    if cmd == "/mode":
        # This is now handled in main loop, but keep for compatibility
        from wx.modes import get_mode_registry
        registry = get_mode_registry()
        current = registry.get_current()
        print(f"현재 모드: {current.label} ({current.icon})")
        print("전환: /mode chat | /mode agent")
        return True, True

    if cmd.startswith("/mode "):
        parts = cmd.split(maxsplit=1)
        if len(parts) >= 2:
            from wx.modes import get_mode_registry
            registry = get_mode_registry()
            try:
                new_mode = registry.switch(parts[1])
                print(f"✓ {new_mode.label}로 전환")
            except Exception as e:
                print(f"✗ {e}")
        return True, True

    # Shortcut commands
    if cmd == "/chat":
        from wx.modes import get_mode_registry
        registry = get_mode_registry()
        registry.switch("chat")
        print("✓ 채팅 모드")
        return True, True

    if cmd == "/agent":
        from wx.modes import get_mode_registry
        registry = get_mode_registry()
        registry.switch("agent")
        print("✓ 에이전트 모드")
        return True, True

    # /open command - list or select a workspace
    if cmd == "/open" or cmd.startswith("/open "):
        parts = raw_cmd.split(maxsplit=1)
        current_workspace = getattr(agent_mode, "workspace", None) or getattr(cfg, "workspace", None) or os.getcwd()
        current_workspace = os.path.abspath(os.path.expanduser(current_workspace))

        folders = []
        if len(parts) == 1:
            try:
                folders = sorted(
                    name for name in os.listdir(current_workspace)
                    if os.path.isdir(os.path.join(current_workspace, name))
                )
            except OSError as exc:
                print(f"[폴더 목록을 읽을 수 없습니다: {exc}]")
                return True, True
            print(f"[현재 워크스페이스: {current_workspace}]")
            if folders:
                print("[하위 폴더]")
                for index, name in enumerate(folders, start=1):
                    print(f"  {index}. {name}")
            else:
                print("[하위 폴더 없음]")
            print("경로 또는 번호를 입력하세요. Enter는 취소합니다.")
            try:
                selection = input("/open > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return True, True
            if not selection:
                return True, True
            if selection.isdecimal() and 1 <= int(selection) <= len(folders):
                path = folders[int(selection) - 1]
            else:
                path = selection
        else:
            path = parts[1].strip()

        if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '\"'):
            path = path[1:-1]
        expanded_path = os.path.expanduser(path)
        target = os.path.abspath(
            expanded_path if os.path.isabs(expanded_path) else os.path.join(current_workspace, expanded_path)
        )
        if not os.path.isdir(target):
            print(f"[폴더를 찾을 수 없습니다: {target}]")
            return True, True

        # Persist workspace selection in the active user configuration.
        import json
        from wx.config import get_user_config_path
        config_path = os.environ.get("WX_CONFIG_PATH") or str(get_user_config_path())
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        cfg_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[설정 파일을 읽을 수 없습니다: {exc}]")
                return True, True
        cfg_data["workspace"] = target
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            print(f"[설정을 저장할 수 없습니다: {exc}]")
            return True, True
        if agent_mode is not None:
            agent_mode.set_workspace(target)
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[워크스페이스 변경: {target}]{brand.RESET}")
        else:
            print(f"  [워크스페이스 변경: {target}]")
        return True, True

    if cmd == "/providers":
        # Launch provider manager
        await run_provider_manager()
        return True, True

    if cmd.startswith("/model"):
        # Check if it's just "/model" (interactive selector) or "/model <name>" (direct)
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            # Launch interactive model selector using new module
            chosen = run_model_selector(
                load_presets_from_config(cfg),
                router.current_model,
                mode=cfg.ui.get("model_selector", "tui") if cfg.ui else "tui",
                color=color,
            )
            if chosen:
                router.config = type(router.config)(
                    primary_model=chosen.id,
                    fallback_chain=router.config.fallback_chain,
                    base_url=chosen.base_url or router.config.base_url,
                    api_key=router.config.api_key,
                    timeout=router.config.timeout,
                    connect_timeout=router.config.connect_timeout,
                    max_retries=router.config.max_retries,
                    provider=chosen.provider,
                )
                router._current_model = chosen.id
                router._tried_models.clear()
                router._attempt = 0
                if color:
                    print(f"  {brand.fg(brand.GRADIENT_START)}[모델 변경: {chosen.name} ({chosen.id})]{brand.RESET}")
                else:
                    print(f"  [모델 변경: {chosen.name} ({chosen.id})]")
            return True, True
        # Direct model change (old behavior)
        new_model = parts[1]
        router.config = type(router.config)(
            primary_model=new_model,
            fallback_chain=router.config.fallback_chain,
            base_url=router.config.base_url,
            api_key=router.config.api_key,
            timeout=router.config.timeout,
            connect_timeout=router.config.connect_timeout,
            max_retries=router.config.max_retries,
            provider=router.config.provider,
        )
        router._current_model = new_model
        router._tried_models.clear()
        router._attempt = 0
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[모델 변경: {new_model}]{brand.RESET}")
        else:
            print(f"  [모델 변경: {new_model}]")
        return True, True

    if cmd == "/help":
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[명령어]{brand.RESET}")
            print("    /clear       - 대화 히스토리 초기화")
            print("    /model       - 모델 선택 (대화형)")
            print("    /model <명>  - 모델 직접 변경")
            print("    /providers   - 프로바이더 관리 (키 입력/해제)")
            print("    /provider <명> - 프로바이더 변경")
            print("    /open [경로] - 워크스페이스 선택")
            print("    /status      - 현재 상태 표시")
            print("    /exit        - 종료 (quit, :q도 가능)")
            print("    /help        - 이 도움말")
        else:
            print("  [명령어]")
            print("    /clear       - 대화 히스토리 초기화")
            print("    /model       - 모델 선택 (대화형)")
            print("    /model <명>  - 모델 직접 변경")
            print("    /providers   - 프로바이더 관리")
            print("    /provider <명> - 프로바이더 변경")
            print("    /open [경로] - 워크스페이스 선택")
            print("    /status      - 현재 상태 표시")
            print("    /exit        - 종료 (quit, :q도 가능)")
            print("    /help        - 이 도움말")
        return True, True

    if cmd == "/status":
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[현재 상태]{brand.RESET}")
            print(f"    모델: {router.current_model}")
            print(f"    프로바이더: {router.config.provider if hasattr(router, 'config') else 'unknown'}")
            print(f"    베이스 URL: {router.config.base_url if hasattr(router, 'config') else 'unknown'}")
        else:
            print("  [현재 상태]")
            print(f"    모델: {router.current_model}")
            print(f"    프로바이더: {router.config.provider if hasattr(router, 'config') else 'unknown'}")
            print(f"    베이스 URL: {router.config.base_url if hasattr(router, 'config') else 'unknown'}")
        return True, True

    if cmd.startswith("/provider"):
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            if color:
                print(f"  {brand.fg(brand.GRADIENT_START)}[사용법: /provider <이름>]{brand.RESET}")
                print("    사용 가능: spacebunny, openrouter, groq, openai")
            else:
                print("  [사용법: /provider <이름>]")
                print("    사용 가능: spacebunny, openrouter, groq, openai")
            return True, True
        provider_name = parts[1].lower()
        valid_providers = ["spacebunny", "openrouter", "groq", "openai"]
        if provider_name not in valid_providers:
            if color:
                print(f"  {brand.fg(brand.GRADIENT_START)}[알 수 없는 프로바이더: {provider_name}]{brand.RESET}")
                print(f"    사용 가능: {', '.join(valid_providers)}")
            else:
                print(f"  [알 수 없는 프로바이더: {provider_name}]")
                print(f"    사용 가능: {', '.join(valid_providers)}")
            return True, True
        
        from wx.config import DEFAULT_PROVIDER, PROVIDER_DEFAULTS
        defaults = PROVIDER_DEFAULTS.get(provider_name, PROVIDER_DEFAULTS[DEFAULT_PROVIDER])
        router.config = type(router.config)(
            primary_model=defaults.get("model", router.config.primary_model),
            fallback_chain=[],  # Clear fallback for provider switch
            base_url=defaults.get("base_url", router.config.base_url),
            api_key=router.config.api_key,
            timeout=router.config.timeout,
            connect_timeout=router.config.connect_timeout,
            max_retries=router.config.max_retries,
            provider=provider_name,
        )
        router._tried_models.clear()
        router._attempt = 0
        if color:
            print(f"  {brand.fg(brand.GRADIENT_START)}[프로바이더 변경: {provider_name}]{brand.RESET}")
        else:
            print(f"  [프로바이더 변경: {provider_name}]")
        return True, True


# Unknown command - show help
    if color:
        print(f"  {brand.fg(brand.GRADIENT_START)}[알 수 없는 커맨드: {cmd}]{brand.RESET}")
        print("  사용 가능: /help, /mode, /chat, /agent, /open, /model, /provider, /providers, /status, /exit, /clear")
    else:
        print(f"  [알 수 없는 커맨드: {cmd}]")
        print("  사용 가능: /help, /mode, /chat, /agent, /open, /model, /provider, /providers, /status, /exit, /clear")
    return True, True


async def run_interactive(args: argparse.Namespace, key: str, cfg) -> int:
    color = not args.no_color and brand.supports_color(sys.stdout)

    # Build router
    router = _build_router_from_args(args, key, cfg)

    # Keyless providers (opencode zen) answer with an empty api_key on purpose.
    if not router.config.api_key and provider_needs_key(router.config.provider):
        print("  ✗ API 키가 설정되지 않았습니다. /providers로 키를 설정하거나 --key 옵션을 사용하세요.")
        return EXIT_NO_KEY

    # Initialize mode registry
    registry = get_mode_registry()
    registry.register(ChatMode)
    registry.register(AgentMode)

    # Initialize shared message history
    messages: list[dict[str, Any]] = []
    system_prompt = args.system_prompt or (
        "당신은 WX, 사용자의 로컬 코드베이스에서 작업하는 코딩 에이전트입니다. "
        "파일을 읽고 변경하기 전에 먼저 읽으세요. 직접적으로: 서문이나 "
        "무엇을 할지에 대한 요약 없이. 무엇이 바뀌었고 왜 바뀌었는지 말하세요."
    )
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Create mode instances with workspace from config
    workspace = cfg.workspace if hasattr(cfg, 'workspace') and cfg.workspace else None
    chat_mode = ChatMode(router, messages)
    agent_mode = AgentMode(router, messages, workspace=workspace)
    registry = get_mode_registry()
    registry.set_instance("chat", chat_mode)
    registry.set_instance("agent", agent_mode)

    # Verify router is set on modes
    if chat_mode.router is None or agent_mode.router is None:
        print("  ✗ 모드에 라우터가 설정되지 않았습니다.")
        return EXIT_ERROR

    # Set initial mode
    current_mode = chat_mode
    registry._current_name = "chat"

    # Show banner once
    if not args.no_banner:
        banner.show()
        print(f"  {brand.DIM}대화형 모드 — /help 로 도움말, /exit 로 종료{brand.RESET if color else ''}")
        print()

    # Show tokenizer warning once per session if heuristic
    from wx.agent.loop import AgentLoop
    temp_loop = AgentLoop(router, system_prompt=system_prompt)
    if temp_loop.tokenizer_mode != "exact":
        print(f"  [warn] tokenizer: {temp_loop.tokenizer_mode} (counts are approximate)")

    status = StatusLine(interactive=True)
    writer = DeltaWriter()
    interrupted_shown = False

    try:
        while True:
            # Get current mode for prompt
            current_mode = registry.get_current()
            mode_prompt = current_mode.get_prompt_prefix()
            if current_mode is agent_mode:
                workspace_name = os.path.basename(agent_mode.workspace.rstrip(os.sep)) or agent_mode.workspace
                mode_prompt = f"{mode_prompt} {workspace_name}"
            prompt = f"{mode_prompt} › "
            if color:
                prompt = f"{brand.fg(brand.GRADIENT_START)}{mode_prompt}{brand.RESET} › "

            try:
                # Write prompt on new line for clean display
                sys.stdout.write("\n" + prompt)
                sys.stdout.flush()
                user_input = sys.stdin.readline()
                if not user_input:  # EOF
                    break
                user_input = user_input.rstrip("\n\r")
            except KeyboardInterrupt:
                print()
                break
            except EOFError:
                break

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                cmd = user_input.strip().lower()
                handled = False

                # /mode command
                if cmd == "/mode":
                    print(f"현재 모드: {registry.get_current().label} ({registry.get_current().icon})")
                    print("전환: /mode chat | /mode agent")
                    handled = True

                elif cmd.startswith("/mode "):
                    parts = cmd.split(maxsplit=1)
                    if len(parts) >= 2:
                        try:
                            new_mode = registry.switch(parts[1])
                            print(f"✓ {new_mode.label}로 전환")
                        except Exception as e:
                            print(f"✗ {e}")
                    handled = True

                # Shortcut commands
                elif cmd == "/chat":
                    registry.switch("chat")
                    print("✓ 채팅 모드")
                    handled = True
                elif cmd == "/agent":
                    registry.switch("agent")
                    print("✓ 에이전트 모드")
                    handled = True

                # /preview command
                elif cmd == "/preview":
                    preview = get_preview()
                    print(preview.render())
                    handled = True

                # Existing commands
                if not handled:
                    continue_loop, handled = await _handle_command(user_input, router, None, messages, color, cfg, agent_mode)
                    if handled:
                        if not continue_loop:
                            break
                        continue

                if handled:
                    continue

            # Check exit commands
            if user_input.lower() in ("exit", "quit", ":q"):
                break

            # Add user message
            messages.append({"role": "user", "content": user_input})

            # Run turn with current mode
            chunks = 0
            error: Error | None = None
            done: TurnDone | None = None
            pieces: list[str] = []
            interrupted_shown = False

            try:
                async for ev in current_mode.run(user_input):
                    if isinstance(ev, str):
                        chunks += 1
                        pieces.append(ev)
                        writer.write(ev)
                    elif isinstance(ev, Notice):
                        writer.flush()
                        status.clear()
                        _notice(ev.text, ev.level, color)
                    elif isinstance(ev, Error):
                        error = ev
                    elif isinstance(ev, StreamInterrupted):
                        if not interrupted_shown:
                            writer.flush()
                            status.clear()
                            retry_msg = " (재시도...)" if ev.retryable else " (실패)"
                            _notice(f"스트림 중단{retry_msg}: {ev.message}", "warn", color)
                            interrupted_shown = True
                    elif isinstance(ev, TurnDone):
                        done = ev

            except KeyboardInterrupt:
                writer.flush()
                status.clear()
                print()
                break
            except StreamError as exc:
                writer.flush()
                status.clear()
                print(f"\n  {exc.kind}: {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                writer.flush()
                status.clear()
                import traceback
                traceback.print_exc()
                print(f"\n  ✗ 예외 발생: {exc}", file=sys.stderr)
                continue

            writer.flush()
            status.clear()

            if done is not None and done.text and not done.text.endswith("\n"):
                print()

            # Show status with mode info
            if done is not None:
                mode_suffix = registry.get_current().get_status_suffix()
                status.show_final(f"{_progress(temp_loop, chunks, router.current_model)} · {mode_suffix}")

            # Add assistant response to history
            if done is not None and done.text:
                messages.append({"role": "assistant", "content": done.text})

            if error is not None:
                print(f"\n  {error.kind}: {error.message}", file=sys.stderr)
                if error.retryable:
                    print("  이 문제는 일시적입니다 — 다시 시도하세요", file=sys.stderr)

    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
        print()

    print(f"  {brand.DIM}종료{brand.RESET if color else ''}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# single turn run
# ---------------------------------------------------------------------------


async def run_turn(args: argparse.Namespace, key: str, cfg) -> int:
    color = not args.no_color and brand.supports_color(sys.stdout)

    router = _build_router_from_args(args, key, cfg)

    loop = AgentLoop(
        router,
        system_prompt=args.system_prompt,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )

    status = StatusLine()
    writer = DeltaWriter()
    chunks = 0
    error: Error | None = None
    done: TurnDone | None = None
    interrupted_shown = False

    try:
        async for ev in loop.run(" ".join(args.prompt)):
            if isinstance(ev, TextDelta):
                chunks += 1
                writer.write(ev.text)
            elif isinstance(ev, Notice):
                writer.flush()
                status.clear()
                _notice(ev.text, ev.level, color)
            elif isinstance(ev, Error):
                error = ev
            elif isinstance(ev, StreamInterrupted):
                if not interrupted_shown:
                    writer.flush()
                    status.clear()
                    retry_msg = " (재시도...)" if ev.retryable else " (실패)"
                    _notice(f"스트림 중단{retry_msg}: {ev.message}", "warn", color)
                    interrupted_shown = True
            elif isinstance(ev, TurnDone):
                done = ev

    except KeyboardInterrupt:
        writer.flush()
        status.clear()
        print("\n  interrupted", file=sys.stderr)
        return EXIT_ERROR
    except StreamError as exc:
        print(f"  {exc.kind}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        writer.close()

    status.clear()

    if done is not None and done.text and not done.text.endswith("\n"):
        print()

    if done is not None and done.text:
        if chunks == 0:
            print(done.text)

    # Show final status
    if done is not None:
        status.show_final(_progress(loop, chunks, router.current_model))

    if error is not None:
        print(f"\n  {error.kind}: {error.message}", file=sys.stderr)
        if error.retryable:
            print("  이 문제는 일시적입니다 — 다시 시도하세요", file=sys.stderr)

    if hasattr(loop.client, "models_tried") and len(loop.client.models_tried) > 1:
        models_str = " → ".join(loop.client.models_tried)
        print(f"  폴백 체인: {models_str}")

    _summary(loop, done, error, color, show_footer=False)
    return EXIT_ERROR if error is not None else EXIT_OK


def _progress(loop: AgentLoop, chunks: int, model: str | None = None) -> str:
    mode = loop.tokenizer_mode
    model_name = model or getattr(loop.client, "current_model", "unknown")
    return f"{model_name} · streaming {chunks} chunks · tokenizer {mode}"


def _notice(text: str, level: str, color: bool) -> None:
    if not color:
        print(f"  [{level}] {text}")
        return
    tint = brand.fg(brand.GRADIENT_START) if level == "info" else brand.fg((0xFF, 0xC8, 0x4D))
    print(f"  {tint}[{level}]{brand.RESET} {brand.DIM}{text}{brand.RESET}")


def _summary(
    loop: AgentLoop, done: TurnDone | None, error: Error | None, color: bool, show_footer: bool = True
) -> None:
    if done is None:
        return
    out_tokens = 0

    usage = done.usage
    parts = [
        f"model {getattr(loop.client, 'current_model', 'unknown')}",
        f"{humanize_ms(done.elapsed_s)}",
        f"{done.chunks} chunks",
        f"~{_humanize_count(out_tokens)} tok out ({loop.tokenizer_mode})",
    ]
    if usage is not None:
        bits = f"in {_humanize_count(usage.prompt_tokens)}"
        if usage.cached_tokens:
            bits += f" ({_humanize_count(usage.cached_tokens)} cached)"
        bits += f" / out {_humanize_count(usage.completion_tokens)}"
        parts.append(bits)

    line = "  " + " · ".join(parts)
    if error is not None:
        line = f"  {'failed'} · " + " · ".join(parts[1:])
        print(line, file=sys.stderr)
    elif color:
        print(brand.fg(brand.GRADIENT_END) + line + brand.RESET)
    else:
        print(line)
    if show_footer:
        print(f"  {brand.DIM}{brand.footer()}{brand.RESET if color else ''}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    brand.enable_utf8_console()

    parser = build_parser()
    args = parser.parse_args(argv)

    if os.environ.get("NO_COLOR") is None and args.no_color:
        os.environ["NO_COLOR"] = "1"

    if args.no_color:
        brand.supports_color = lambda stream=None: False  # type: ignore[assignment]

    if args.doctor:
        return run_doctor()

    if args.learn:
        return run_learn(args.learn_days)

    # Determine mode: interactive if no prompt or "chat" as first arg
    is_interactive = not args.prompt or (len(args.prompt) == 1 and args.prompt[0].lower() == "chat")

    if is_interactive:
        if not args.no_banner:
            banner.show()

        cfg = load_config()
        key = _find_key(args.key, cfg)
        if not key and provider_needs_key(cfg.provider):
            print_no_key(args)
            return EXIT_NO_KEY

        try:
            return asyncio.run(run_interactive(args, key, cfg))
        except StreamError as exc:
            print(f"  {exc.kind}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        except KeyboardInterrupt:
            return EXIT_OK

    # Single-turn mode (existing behavior)
    if not args.prompt:
        if not args.no_banner:
            banner.show(force=not args.no_banner)
        parser.print_help()
        return EXIT_OK

    if not args.no_banner:
        banner.show()

    cfg = load_config()
    key = _find_key(args.key, cfg)
    if not key and provider_needs_key(cfg.provider):
        print_no_key(args)
        return EXIT_NO_KEY

    try:
        return asyncio.run(run_turn(args, key, cfg))
    except StreamError as exc:
        print(f"  {exc.kind}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
