"""WUXICODE — 선불(prepaid) 크레딧 판정.

ULTIMATE만 billing_type="prepaid"다. 잔액이 유일한 마침표이며, 구독 티어의
daily_tokens/monthly_tokens 상한과는 다른 축이다. 두 축은 AND로 걸린다.

접근 순서:

    allowed, remaining = await check_credits(db, key, est_tokens)   # 사전 확인
    if not allowed:
        return 402
    ... 모델 호출 ...
    await consume_credits(db, key, actual_tokens)                   # 사후 차감

사전 확인은 스펙 개요일 뿐이다. 추정이 빗나가면 사후 차감이 잔액을 음수로
밀어낼 수 있는데, db.decrement_credits는 CHECK 제약으로 막는다. 그래서 호출자는
check_credits를 "빠른 거절"용으로만 쓰고, 권위는 consume_credits에 둔다.
"""

from __future__ import annotations

from license_server.db import DEFAULT_CREDIT_AMOUNT, InsufficientCredits, LicenseDB

__all__ = [
    "AUTO_RELOAD_THRESHOLD",
    "DEFAULT_CREDIT_AMOUNT",
    "InsufficientCredits",
    "balance",
    "check_credits",
    "consume_credits",
    "ensure_account",
    "set_auto_reload",
]

# 잔액이 이 아래로 내려가면 자동 충전이 켜져 있으면 한 번 채운다.
# 잔액이 0으로 떨어졌다가 채워지는 걸 허용하되, 매 호출마다 충전되어
# 무한 루프가 되지 않도록 consume_credits에서 호출 1회로 제한한다.
AUTO_RELOAD_THRESHOLD = 1_000_000


async def ensure_account(db: LicenseDB, key: str) -> None:
    """계정을 만든다. 이미 있으면 아무 일도 없다."""
    db.ensure_credit(key)


async def balance(db: LicenseDB, key: str) -> int:
    """현재 잔액. 계정이 없으면 0."""
    return db.balance(key)


async def check_credits(db: LicenseDB, key: str, est_tokens: int) -> tuple[bool, int]:
    """선불 잔액 확인.

    Returns:
        (가능 여부, 호출 후 남을 잔액)
        잔액이 est_tokens보다 적으면 (False, 현재 잔액)을 돌려준다.
    """
    if est_tokens < 0:
        raise ValueError("est_tokens는 음수가 될 수 없다")
    row = db.get_credit(key)
    if row is None:
        return False, 0
    if row.balance_tokens < est_tokens:
        return False, row.balance_tokens
    return True, row.balance_tokens - est_tokens


async def consume_credits(db: LicenseDB, key: str, actual_tokens: int) -> int:
    """실제 사용량을 차감하고 자동 충전 여부를 확인한다.

    Returns:
        차감 후 잔액 (자동 충전이 작동했다면 충전된 뒤의 잔액)

    잔액이 부족하면 InsufficientCredits가 난다. 호출자는 그 예외를 402로
    바꿔 클라이언트에 보내야 한다.
    """
    if actual_tokens < 0:
        raise ValueError("actual_tokens는 음수가 될 수 없다")

    remaining = db.decrement_credits(key, actual_tokens)

    # 자동 충전은 호출당 최대 한 번. threshold 미만일 때만.
    row = db.get_credit(key)
    if (
        row is not None
        and row.auto_reload
        and row.balance_tokens < AUTO_RELOAD_THRESHOLD
    ):
        amount = row.auto_reload_amount or DEFAULT_CREDIT_AMOUNT
        remaining = db.increment_credits(key, amount)

    return remaining


async def set_auto_reload(
    db: LicenseDB,
    key: str,
    enabled: bool,
    amount: int | None = None,
):
    """자동 충전을 켜거나 끈다."""
    return db.set_auto_reload(key, enabled, amount)
