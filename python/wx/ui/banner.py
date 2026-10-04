"""WUXICODE — first-run banner.

Shown once per machine, not once per invocation. A banner that prints on every
run trains people to pipe output, and piped output must not contain escape
sequences. It is rendered before any prompt, and the `made by ujin` credit
sits in the bottom-right of the block as required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import brand

__all__ = ["state_path", "should_show", "mark_shown", "render", "show"]

_STATE_VERSION = 1

# Left column: WX monogram. Six rows, so the right column is vertically centred
# against it.
_MONOGRAM = (
    "██╗    ██╗ ██████╗ ",
    "██║    ██║██╔═══██╗",
    "██║ █╗ ██║██║   ██║",
    "██║███╗██║██║   ██║",
    "╚███╔██╔╝╚█████╔╝",
    " ╚══╝    ╚════╝  ",
)

_WORDMARK = "W U X I C O D E"
_TAGLINE = "hybrid coding agent"

# CP949 (the default Korean Windows codepage) cannot encode the block glyphs.
# Falling back to ASCII is not cosmetic: printing the Unicode monogram there
# raises UnicodeEncodeError and takes the whole CLI down.
_MONOGRAM_ASCII = (
    r" __      __   _____  ",
    r"|  |    |  | |  _  \ ",
    r"| | | | |  | | | |_) |",
    r"| |_| | |  | |  _  <  ",
    r" \___/  |__| |_| \_\ ",
)


def state_dir() -> Path:
    """Per-user state directory.

    Windows uses %LOCALAPPDATA% rather than %APPDATA% because this holds
    machine-local cache and first-run state, not roaming documents.
    """
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(root) / "wuxicode"


def state_path() -> Path:
    return state_dir() / "state.json"


def _read_state() -> dict:
    try:
        with state_path().open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def should_show() -> bool:
    """True the first time on this machine, and if state cannot be persisted."""
    if os.environ.get("WUXICODE_NO_BANNER"):
        return False
    return _read_state().get("banner_shown") is not True


def mark_shown() -> bool:
    """Record that the banner has been displayed.

    Returns False if the state could not be written. The caller treats that as
    "show again next run" — a banner that repeats is a smaller problem than a
    banner that silently never appears.
    """
    state = _read_state()
    state["banner_shown"] = True
    state["version"] = state.get("version", 1)
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        with state_path().open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


def _version_line() -> str:
    from wx import __version__ as v

    return f"v{v} · {brand.AI_BRAND} · {brand.FREE_TIER_NAME}"


def render(*, color: bool | None = None, unicode_ok: bool | None = None) -> str:
    """Compose the banner as a block of text, already colored if supported."""
    if color is None:
        color = brand.supports_color()
    if unicode_ok is None:
        unicode_ok = brand.supports_unicode()

    monogram = list(_MONOGRAM if unicode_ok else _MONOGRAM_ASCII)

    right = [
        (
            brand.BOLD + brand.gradient_text(_WORDMARK, color=color) + brand.RESET
            if color
            else _WORDMARK
        ),
        brand.DIM + _TAGLINE + brand.RESET if color else _TAGLINE,
        "",
        _version_line(),
    ]

    pad = (len(monogram) - len(right)) // 2
    if pad < 0:
        pad = 0

    right = [""] * pad + right
    height = max(len(monogram), len(right))

    rows: list[str] = []
    for i in range(height):
        left = monogram[i] if i < len(monogram) else ""
        if color:
            left = fg_for_monogram(i, left)
        r = right[i] if i < len(right) else ""
        gap = max(2, 22 - brand.len_visible(left))
        rows.append(left + " " * gap + r)

    bar = brand.gradient_bar(max(22, brand.len_visible(rows[0])), color=color)
    if not unicode_ok:
        bar = brand.gradient_bar(max(22, brand.len_visible(rows[0])), color=color).replace(
            "─", "-"
        )
    credit = brand.DIM + brand.footer() + brand.RESET if color else brand.footer()

    return "\n".join([bar, *rows, bar, credit])


def fg_for_monogram(row: int, glyphs: str) -> str:
    """Tint the monogram with the brand gradient, top row violet, bottom cyan."""
    if not glyphs.strip():
        return glyphs
    t = row / 5.0
    rgb = brand.lerp(brand.GRADIENT_START, brand.GRADIENT_END, t)
    return brand.fg(rgb) + glyphs + brand.RESET


def show(*, force: bool = False, color: bool | None = None) -> bool:
    """Print the banner if this is a first run. Returns whether it printed."""
    if not force and not should_show():
        return False
    print(render(color=color))
    if not force:
        mark_shown()
    return True