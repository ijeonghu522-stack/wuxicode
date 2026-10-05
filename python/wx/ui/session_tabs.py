"""WUXICODE — 세션 탭 바.

Claude Code / Codex 계열의 대화형 UI가 대화 위에서 고리를 두르는 부분이다.
WUXICODE는 아직 FTXUI TUI가 아니라(Phase 4) 라인 단위 출력이라, 탭 바는
프롬프트 바로 위에 한 줄로 찍히고 상태 줄은 그 응답 아래에 찍힌다.

여기서 그리는 탭은 상태 표시다. 탭을 만들거나 닫는 실제 동작은
wx.modes.session_manager가 하고, 이 모듈은 그 결과를 문자열로 옮길
뿐이다.
"""

from __future__ import annotations

from wx.ui import brand
from wx.ui.width import pad_to, str_width, truncate_to

__all__ = [
    "MAX_VISIBLE_TABS",
    "render_panel",
    "render_status_line",
    "render_tab_bar",
]

# 탭이 많아지면 이름을 접는다. 가운데를 자르면 활성 탭이 사라지므로
# 왼쪽부터 자르고, 잘렸으면 앞쪽에 '…'을 남긴다.
MAX_VISIBLE_TABS = 6


def _tab_label(name: str, active: bool) -> str:
    """탭 하나를 [name] 형태로. 활성 탭은 '▸'을 붙인다."""
    return f"▸{name}" if active else name


def render_tab_bar(
    names: list[str],
    active_index: int = 0,
    *,
    width: int = 80,
    color: bool = False,
    show_add: bool = True,
) -> str:
    """탭 한 줄.

        ▸main  프로젝트1  +
    """
    if not names:
        return ("+ 새 세션" if show_add else "")

    # 활성 탭이 항상 보이도록, 넘치면 활성 탭부터 왼쪽에 남긴다.
    visible = list(names[:MAX_VISIBLE_TABS])
    hidden = len(names) - len(visible)
    active_visible = active_index < len(visible)

    parts: list[str] = []
    for i, name in enumerate(visible):
        label = _tab_label(name, i == active_index)
        parts.append(f"[{label}]" if not color else label)

    if hidden > 0:
        parts.insert(0, f"[+{hidden}]")

    line = " ".join(parts)
    if show_add:
        line = f"{line} [+]"

    line = truncate_to(line, max(8, width))
    if not active_visible:
        # 활성 탭이 접힌 부분에 있다 — 마커만 맨 앞에 둔다.
        line = "▸* " + line
    if color:
        return f"{brand.fg(brand.GRADIENT_START)}{line}{brand.RESET}"
    return line


def render_status_line(
    *,
    tokens_used: int,
    tokens_limit: int | None,
    tier_label: str,
    active_sessions: int = 1,
    max_concurrent: int = 1,
    color: bool = False,
) -> str:
    """탭 바 아래 한 줄 상태.

        토큰: 1,234 / 50,000 · 무료 · 세션 1/1
    """
    from wx.ui.token_meter import format_tokens

    if tokens_limit:
        tokens = f"{format_tokens(tokens_used)} / {format_tokens(tokens_limit)}"
    else:
        tokens = format_tokens(tokens_used)

    line = f"토큰: {tokens} · {tier_label} · 세션 {active_sessions}/{max_concurrent}"
    if color:
        return f"{brand.DIM}{line}{brand.RESET}"
    return line


def render_panel(
    tab_line: str,
    body_lines: list[str],
    *,
    status_line: str = "",
    title: str = "WUXICODE",
    width: int = 41,
    color: bool = False,
) -> str:
    """탭 바 + 본문 + 상태 줄을 감싸는 박스.

        ┌─ WUXICODE ────────────────┐
        │ ▸main  프로젝트1  [+]     │
        ├───────────────────────────┤
        │ [CHAT] › 안녕              │
        │ 안녕하세요! ...            │
        ├───────────────────────────┤
        │ 토큰: 1,234 / 50,000 ...  │
        └───────────────────────────┘

    tab_line은 render_tab_bar()가 만들어 준 문자열을 그대로 받는다.
    너비는 표시 칸 수 기준으로 맞춘다. 한글은 2칸이므로 len()으로 세면
    오른쪽 테두리가 딸려간다 — wx.ui.width를 쓴 이유다.
    """
    inner = max(20, width - 4)
    ul, ur = ("┌", "┐") if brand.supports_unicode() else ("+", "+")
    ll, lr = ("└", "┘") if brand.supports_unicode() else ("+", "+")
    v = "│" if brand.supports_unicode() else "|"
    h = "─" if brand.supports_unicode() else "-"
    tee_l, tee_r = ("├", "┤") if brand.supports_unicode() else ("+", "+")

    def row(text: str) -> str:
        return f"{v}{pad_to(truncate_to(' ' + text, inner), inner)}{v}"

    # 전체 폭 = 테두리 2칸 + inner. 윗변·가운데선·아랫변은 모두 같은 폭이어야
    # 세로선이 어긋나지 않는다.
    title_text = f" {title} " if title else ""
    dash = max(0, inner - str_width(title_text))
    top = f"{ul}{title_text}{h * dash}{ur}" if title else f"{ul}{h * inner}{ur}"
    mid = f"{tee_l}{h * inner}{tee_r}"
    bottom = f"{ll}{h * inner}{lr}"

    lines = [top, row(tab_line)]
    lines.append(mid)
    for text in body_lines:
        lines.append(row(text))
    if status_line:
        lines.append(mid)
        lines.append(row(status_line))
    lines.append(bottom)
    return "\n".join(lines)