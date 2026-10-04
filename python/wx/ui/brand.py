"""WUXICODE — the single source of every user-visible brand string.

Rule that keeps this file honest: if a string reaches a user's eyes anywhere
in WUXICODE — CLI banner, status bar, help text, error message, web footer,
icon tooltip — it is defined here and nowhere else. `version.json` and
`pyproject.toml` carry build identity only. Two copies of "made by ujin" is
how a footer ends up saying something the banner does not.
"""

from __future__ import annotations

import ctypes
import os
import sys

__all__ = [
    "MADE_BY",
    "CONTACT_EMAIL",
    "PRODUCT_NAME",
    "AI_BRAND",
    "FREE_TIER_NAME",
    "PAID_TIER_NAME",
    "PAID_PRICE_KRW",
    "PAID_INTERVAL",
    "GRADIENT_START",
    "GRADIENT_END",
    "RESET",
    "BOLD",
    "DIM",
    "supports_color",
    "supports_unicode",
    "enable_utf8_console",
    "separator",
    "ellipsis",
    "rule",
    "fg",
    "lerp",
    "gradient_text",
    "gradient_bar",
    "len_visible",
]

# --- identity ---------------------------------------------------------------
# These three strings are the contract. If they change, they change here.
MADE_BY = "made by ujin"
CONTACT_EMAIL = "ijeonghu522@gmail.com"

PRODUCT_NAME = "WUXICODE"
AI_BRAND = "WX"

# --- tiers ------------------------------------------------------------------
# WX AI is the free tier. W_X ZEN is the paid one, sharing the W_X brand with
# the separate hardware toolkit. Same name, separate product, separate license.
FREE_TIER_NAME = "WX AI"
PAID_TIER_NAME = "W_X ZEN"
PAID_PRICE_KRW = 15_000
PAID_INTERVAL = "month"

# --- palette ----------------------------------------------------------------
# Brand gradient, violet -> cyan.
GRADIENT_START = (0x7C, 0x3A, 0xED)
GRADIENT_END = (0x06, 0xB6, 0xD4)

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

_IS_WINDOWS = sys.platform == "win32"
_vt_checked = False
_vt_available = False

# Decorative glyphs used by the banner. A Korean Windows console defaults to
# CP949, which cannot encode any of these — printing the banner there is an
# immediate UnicodeEncodeError, not a cosmetic problem. Every one of them has
# an ASCII fallback in banner.py.
_DECORATIVE = "█▀▄│─├┤┐┘╭╮╰╯·…═"


def enable_utf8_console() -> bool:
    """Switch the console output codepage to UTF-8 and retarget the streams.

    Two steps, both required. SetConsoleOutputCP changes what the console
    interprets, but CPython caches the stream encoding at startup, so the
    streams still encode as cp949 afterwards and raise UnicodeEncodeError.
    Reconfiguring the streams is what actually makes the bytes match.

    Returns True when the streams are UTF-8 afterwards.
    """
    ok = True
    if _IS_WINDOWS:
        try:
            import ctypes

            CP_UTF8 = 65001
            if not ctypes.windll.kernel32.SetConsoleOutputCP(CP_UTF8):
                ok = False
        except Exception:
            ok = False

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            ok = False
            continue
        try:
            # errors="replace" is the floor: a banner that degrades to '?' beats
            # a traceback on someone's console.
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            ok = False
    return ok


def supports_unicode(stream=None) -> bool:
    """True when the stream's encoding can represent the decorative glyphs."""
    if os.environ.get("WUXICODE_ASCII"):
        return False
    target = stream if stream is not None else sys.stdout
    encoding = getattr(target, "encoding", None) or "ascii"
    try:
        _DECORATIVE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def separator() -> str:
    """Middle dot when the stream can encode it, hyphen when it cannot."""
    return "·" if supports_unicode() else "-"


def ellipsis() -> str:
    return "…" if supports_unicode() else "..."


def rule(width: int = 20) -> str:
    """A plain rule that always encodes."""
    return ("─" if supports_unicode() else "-") * max(width, 0)


def footer() -> str:
    """The credit line. Fixed at the bottom-right of every screen."""
    return f"{MADE_BY} {separator()} {CONTACT_EMAIL}"


def purchase_hint() -> str:
    """One line about upgrading. Contact address is the purchase channel."""
    return (
        f"{PAID_TIER_NAME} — ₩{PAID_PRICE_KRW:,}/{PAID_INTERVAL} {separator()} "
        f"purchase or questions: {CONTACT_EMAIL}"
    )


def _enable_windows_vt() -> bool:
    """Turn on ANSI escape processing for the Windows console.

    Without this, every escape sequence prints literally on cmd.exe and
    PowerShell 5.1. Windows 10 1511+ supports it; older consoles fall back to
    plain text rather than a screen full of escape codes.
    """
    if not _IS_WINDOWS:
        return False

    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    STD_OUTPUT_HANDLE = -11
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    if handle in (0, -1, None):
        return False

    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False

    if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return True

    return bool(
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    )


def supports_color(stream=None) -> bool:
    """True when ANSI escapes will render as color rather than as text."""
    global _vt_checked, _vt_available

    if _IS_WINDOWS:
        if not _vt_checked:
            _vt_available = _enable_windows_vt()
            _vt_checked = True
        if not _vt_available:
            return False

    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False

    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def fg(rgb: tuple[int, int, int]) -> str:
    """24-bit foreground color escape."""
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Linear interpolation between two RGB points. t is clamped to [0, 1]."""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


# Backwards-compatible private alias.
_lerp = lerp


def gradient_text(text: str, *, color: bool | None = None) -> str:
    """Paint text with the brand gradient, one ramp step per character.

    Escape sequences are emitted only when the color actually changes, so a
    gradient over a long string is bounded by the number of distinct steps
    rather than by its length.
    """
    if color is None:
        color = supports_color()
    if not color or not text:
        return text

    n = len(text)
    out: list[str] = []
    last: tuple[int, int, int] | None = None

    for i, ch in enumerate(text):
        t = i / (n - 1) if n > 1 else 0.0
        rgb = _lerp(GRADIENT_START, GRADIENT_END, t)
        if rgb != last:
            out.append(fg(rgb))
            last = rgb
        out.append(ch)

    out.append(RESET)
    return "".join(out)


def gradient_bar(width: int = 24, *, color: bool | None = None) -> str:
    """A horizontal rule painted with the brand gradient."""
    if color is None:
        color = supports_color()
    if not color or width <= 0:
        return "─" * max(width, 0)

    out: list[str] = []
    last: tuple[int, int, int] | None = None
    for i in range(width):
        rgb = _lerp(GRADIENT_START, GRADIENT_END, i / max(width - 1, 1))
        if rgb != last:
            out.append(fg(rgb))
            last = rgb
        out.append("─")
    out.append(RESET)
    return "".join(out)


def len_visible(text: str) -> int:
    """Length of `text` with ANSI escapes stripped.

    Padding and column alignment have to be computed against visible width or
    the banner wraps on exactly the terminals that support color.
    """
    out = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            j = text.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out