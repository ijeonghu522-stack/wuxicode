"""WUXICODE — 토큰 사용량 미터.

매 응답 뒤 화면 아래에 한 줄씩 붙는 계기판이다. 볼 일은 셋: 지금까지
얼마를 썼나, 그 티어의 한도가 얼마인가, 세션이 몇 개 열려 있나.

한도 대신 크레딧 잔액이 기준인 티어가 있어서 limit에 None이 들어올 수
있다(ULTIMATE). 그때는 막대 대신 잔액 문자열만 찍는다 — 0원이 아니라
크레딧 잔액을 %로 환산하면 오독이 생긴다.
"""

from __future__ import annotations

from dataclasses import dataclass

from wx.ui import brand

__all__ = [
    "BAR_WIDTH",
    "MeterState",
    "format_bytes",
    "format_tokens",
    "render_meter",
]

BAR_WIDTH = 20

# 막대 색: 정상 -> 경고 -> 소진 임박. brand 그라디언트와 같은 계열.
_GOOD = (0x7C, 0xFF, 0x3B)
_WARN = (0xFF, 0xB0, 0x20)
_HOT = (0xFF, 0x55, 0x55)


def format_tokens(value: int) -> str:
    """12345 -> '12,345', 1200000 -> '1.2M'.

    6자리 밑은 세 자리 구분자로 읽히고, 그 위로는Compact가 줄을 깨지 않는다.
    """
    if value < 0:
        return "0"
    if value < 1_000_000:
        return f"{value:,}"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value / 1_000_000_000:.1f}B"


def format_bytes(value: int) -> str:
    """16071 -> '16,071 B', 1048576 -> '1.0 MB'."""
    if value < 1024:
        return f"{value:,} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


@dataclass(slots=True)
class MeterState:
    """미터를 그리는 데 필요한 값. 전부 호출부가 채워 넣는다."""

    tokens_used: int = 0
    tokens_limit: int | None = None
    tier_name: str = "WX (무료)"
    model: str | None = None
    active_sessions: int = 1
    max_concurrent: int = 1
    total_sessions: int = 1
    max_sessions: int = 3
    balance: int | None = None  # 선불 티어 잔액. None이면 구독 티어.


def _percent(used: int, limit: int) -> int:
    if limit <= 0:
        return 100
    return max(0, min(100, int(used * 100 / limit)))


def _bar(pct: int, width: int, color: bool) -> str:
    filled = int(round(width * pct / 100))
    body = "█" * filled + "░" * (width - filled)
    if not color or not brand.supports_unicode():
        return f"[{body if brand.supports_unicode() else '#' * filled + '-' * (width - filled)}]"
    rgb = _HOT if pct >= 90 else _WARN if pct >= 70 else _GOOD
    return (
        f"{brand.fg(rgb)}[{'█' * filled}{brand.DIM}{'░' * (width - filled)}{brand.RESET}]"
    )


def render_meter(state: MeterState, *, width: int = 41, color: bool = False) -> str:
    """미터 전체를 여러 줄 문자열로 그린다.

    예시(무료 티어, 24% 사용):

        ─────────────────────────────────────────
        토큰: 12,345 / 50,000 (24%)
        [███████░░░░░░░░░░░░░░░]
        티어: WX (무료) · 모델: space-bunny-free
        세션: 1/1 (동시), 1/3 (총)
        ─────────────────────────────────────────
    """
    rule = "─" if brand.supports_unicode() else "-"
    bar_rule = rule * max(8, min(width, 80))

    lines: list[str] = []

    # 첫 줄: 사용량. 한도가 없으면 크레딧 잔액을 그대로 보여준다.
    if state.tokens_limit:
        pct = _percent(state.tokens_used, state.tokens_limit)
        head = (
            f"토큰: {format_tokens(state.tokens_used)} / "
            f"{format_tokens(state.tokens_limit)} ({pct}%)"
        )
        meter_line = _bar(pct, BAR_WIDTH, color)
    elif state.balance is not None:
        head = f"크레딧: {format_tokens(state.balance)} 남음 (선불)"
        meter_line = ""
    else:
        head = f"토큰: {format_tokens(state.tokens_used)}"
        meter_line = ""

    second = f"티어: {state.tier_name}"
    if state.model:
        second += f" · 모델: {state.model}"
    third = (
        f"세션: {state.active_sessions}/{state.max_concurrent} (동시), "
        f"{state.total_sessions}/{state.max_sessions} (총)"
    )

    if color:
        tint = brand.fg(brand.GRADIENT_START)
        lines.append(brand.DIM + bar_rule + brand.RESET)
        lines.append(f"{tint}{head}{brand.RESET}")
        if meter_line:
            lines.append(meter_line)
        lines.append(brand.DIM + second + brand.RESET)
        lines.append(brand.DIM + third + brand.RESET)
        lines.append(brand.DIM + bar_rule + brand.RESET)
    else:
        lines.append(bar_rule)
        lines.append(head)
        if meter_line:
            lines.append(meter_line)
        lines.append(second)
        lines.append(third)
        lines.append(bar_rule)

    return "\n".join(lines)


def render_meter_line(state: MeterState, *, color: bool = False) -> str:
    """박스 안에 넣을 한 줄짜리 버전.

    탭 패널 하단에 쓰는 축약형. 세션 탭 UI에서 막대까지 넣으면 두 줄을
    먹으므로, 여기서는 숫자만 남긴다.
    """
    if state.tokens_limit:
        pct = _percent(state.tokens_used, state.tokens_limit)
        head = (
            f"토큰: {format_tokens(state.tokens_used)} / "
            f"{format_tokens(state.tokens_limit)} ({pct}%)"
        )
    elif state.balance is not None:
        head = f"크레딧: {format_tokens(state.balance)} 남음"
    else:
        head = f"토큰: {format_tokens(state.tokens_used)}"

    line = f"{head} · {state.tier_name} · 세션 {state.active_sessions}/{state.max_concurrent}"
    if color:
        return f"{brand.DIM}{line}{brand.RESET}"
    return line