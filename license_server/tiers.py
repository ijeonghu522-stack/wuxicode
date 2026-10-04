"""WUXICODE — 라이선스 요금제 정의.

이 모듈이 요금제의 단일 원천(single source of truth)이다.
서버(license_server)와 클라이언트(python/wx/license) 모두 여기서
정의한 값을 읽어야 하며, 양쪽에 복사해서 두지 않는다.

티어 상향/하향은 서버가 이 값을 기준으로 판단하고, 클라이언트는
서버가 서명한 토큰에 담긴 entitlements만 신뢰한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(Enum):
    FREE = "free"
    PLUS = "plus"
    ZEN = "zen"


@dataclass(frozen=True)
class TierConfig:
    name: str
    price_krw: int
    price_usd: int
    # FREE는 라이선스 키가 필요 없으므로 None이다.
    key_prefix: str | None
    daily_tokens: int
    monthly_tokens: int
    max_concurrent: int
    can_write_files: bool
    can_run_bash: bool
    can_use_git: bool
    can_preview: bool
    models: tuple[str, ...]
    priority_support: bool

    def allows_model(self, model: str) -> bool:
        """이 티어에서 model을 쓸 수 있는지."""
        return model in self.models

    def allows_tool(self, tool: str) -> bool:
        """이 티어에서 tool을 쓸 수 있는지.

        읽기 계열(read, glob, grep)은 모든 티어에서 허용된다.
        나머지는 티어별 플래그를 따른다.
        """
        if tool in ("read", "glob", "grep", "list"):
            return True
        if tool in ("write", "edit"):
            return self.can_write_files
        if tool == "bash":
            return self.can_run_bash
        if tool in ("git_status", "git_diff", "git_log", "git_commit", "git_push"):
            return self.can_use_git
        # 알 수 없는 도구는 보수적으로 거부한다.
        return False


TIERS = {
    Tier.FREE: TierConfig(
        name="WX",
        price_krw=0,
        price_usd=0,
        key_prefix=None,  # 라이선스 불필요
        daily_tokens=50_000,
        monthly_tokens=1_000_000,
        max_concurrent=1,
        can_write_files=False,
        can_run_bash=False,
        can_use_git=False,
        can_preview=False,
        models=("wx-1",),
        priority_support=False,
    ),
    Tier.PLUS: TierConfig(
        name="WX PLUS",
        price_krw=13_000,
        price_usd=10,
        key_prefix="WXP",
        daily_tokens=500_000,
        monthly_tokens=10_000_000,
        max_concurrent=3,
        can_write_files=True,
        can_run_bash=True,
        can_use_git=True,
        can_preview=True,
        models=("wx-1", "space-bunny-free"),
        priority_support=False,
    ),
    Tier.ZEN: TierConfig(
        name="WX ZEN",
        price_krw=20_000,
        price_usd=15,
        key_prefix="WXZ",
        daily_tokens=2_000_000,
        monthly_tokens=50_000_000,
        max_concurrent=10,
        can_write_files=True,
        can_run_bash=True,
        can_use_git=True,
        can_preview=True,
        models=(
            "wx-1",
            "space-bunny-free",
            "qwen/qwen3.8-27b",
            "gpt-4o-mini",
            "claude-sonnet",
        ),
        priority_support=True,
    ),
}


# 키 접두사 -> 티어. WXP- 로 시작하면 PLUS, WXZ- 면 ZEN.
PREFIX_TO_TIER = {
    cfg.key_prefix: tier for tier, cfg in TIERS.items() if cfg.key_prefix is not None
}


def tier_from_prefix(prefix: str) -> Tier | None:
    """키 접두사로 티어를 역산한다. 알 수 없으면 None."""
    return PREFIX_TO_TIER.get(prefix.upper())


def get_tier(tier: Tier | str) -> TierConfig:
    """Tier enum 또는 문자열로 설정을 가져온다."""
    if isinstance(tier, str):
        try:
            tier = Tier(tier.lower())
        except ValueError as exc:
            raise ValueError(f"알 수 없는 티어: {tier!r}") from exc
    return TIERS[tier]


def tier_from_value(value: str) -> Tier:
    """'zen' 또는 'WXZ' 같은 값으로 티어를 찾는다."""
    key = value.strip().upper()
    if key in PREFIX_TO_TIER:
        return PREFIX_TO_TIER[key]
    try:
        return Tier(key.lower())
    except ValueError as exc:
        raise ValueError(f"알 수 없는 티어: {value!r}") from exc