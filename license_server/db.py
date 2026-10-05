"""WUXICODE — 라이선스 서버 저장소.

credits 테이블은 선불(prepaid) 티어의 토큰 잔액을 담는다. ULTIMATE는
월 구독이 아니라 $25 = 5M 토큰 크레딧을 사고, 쓰면 줄고, 0이 되면 접근이
멈춘다. 구독 티어(FREE/PLUS/ZEN)의 한도는 여기 저장하지 않는다. 그건
tiers.py의 daily_tokens/monthly_tokens가 단일 원천이고, 서버 계정의
사용량 집계와 합쳐서 판정한다.

동시성: 잔액 차감은 반드시 BEGIN IMMEDIATE 안에서 읽고-쓰기를 함께 한다.
WAL 모드라 읽기는 writers를 막지 않고, 차감 직후의 balance를 같은 트랜잭션에서
반환하면 동시 요청이 서로 같은 잔액을 두 번 쓰는 일이 없다. 잔액은 음수가
될 수 없으므로 CHECK 제약으로 DB가 마지막 방어선을 맡는다.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "DEFAULT_CREDIT_AMOUNT",
    "CreditRow",
    "InsufficientCredits",
    "LicenseDB",
    "default_db_path",
]

# $25 충전 시 들어가는 크레딧. tiers.py의 ULTIMATE 일일/월간 한도와 무관한 값이다.
DEFAULT_CREDIT_AMOUNT = 5_000_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credits (
    key                TEXT PRIMARY KEY,
    balance_tokens     INTEGER NOT NULL DEFAULT 0 CHECK (balance_tokens >= 0),
    last_reload_at     TIMESTAMP,
    auto_reload        BOOLEAN NOT NULL DEFAULT 0,
    auto_reload_amount INTEGER NOT NULL DEFAULT 5000000 CHECK (auto_reload_amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_credits_last_reload ON credits (last_reload_at);
"""


@dataclass(frozen=True)
class CreditRow:
    """credits 테이블 한 행."""

    key: str
    balance_tokens: int
    last_reload_at: str | None
    auto_reload: bool
    auto_reload_amount: int


def default_db_path() -> Path:
    """DB 파일 경로. WX_LICENSE_DB 환경변수로 덮어쓸 수 있다."""
    override = os.environ.get("WX_LICENSE_DB")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "data" / "wuxicode.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InsufficientCredits(ValueError):
    """잔액이 차감량보다 적을 때.

    API 계층은 이 예외를 402로 변환한다. 잔액이 부족하다는 사실과
    얼마가 남았는지를 함께 알린다.
    """

    def __init__(self, key: str, balance: int, requested: int) -> None:
        super().__init__(f"크레딧 부족: 잔액 {balance:,} < 요청 {requested:,} (key={key})")
        self.key = key
        self.balance = balance
        self.requested = requested


class LicenseDB:
    """SQLite 기반 크레딧 저장소.

    컨텍스트 매니저로 쓰는 것을 전제로 한다:

        with LicenseDB() as db:
            db.increment_credits("WXU-...", 5_000_000)
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: 트랜잭션 경계를 직접 연다.
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    # --- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> LicenseDB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- reads --------------------------------------------------------------

    def get_credit(self, key: str) -> CreditRow | None:
        """계정 잔액을 읽는다. 없으면 None."""
        row = self._conn.execute(
            "SELECT key, balance_tokens, last_reload_at, auto_reload, auto_reload_amount "
            "FROM credits WHERE key = ?",
            (key,),
        ).fetchone()
        return self._to_row(row) if row is not None else None

    def balance(self, key: str) -> int:
        """잔액. 계정이 없으면 0."""
        row = self.get_credit(key)
        return row.balance_tokens if row is not None else 0

    def list_auto_reload(self, limit: int = 100) -> list[CreditRow]:
        """자동 충전이 켜진 계 목록. 운영 점검용."""
        rows = self._conn.execute(
            "SELECT key, balance_tokens, last_reload_at, auto_reload, auto_reload_amount "
            "FROM credits WHERE auto_reload = 1 ORDER BY key LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_row(r) for r in rows]

    # --- writes -------------------------------------------------------------

    def ensure_credit(self, key: str) -> CreditRow:
        """계정을 없으면 잔액 0으로 만든다. 있는 계정은 그대로 둔다."""
        self._conn.execute(
            "INSERT INTO credits (key, balance_tokens, auto_reload, auto_reload_amount) "
            "VALUES (?, 0, 0, ?) ON CONFLICT(key) DO NOTHING",
            (key, DEFAULT_CREDIT_AMOUNT),
        )
        row = self.get_credit(key)
        assert row is not None  # 방금 넣었으므로 항상 존재한다
        return row

    def set_balance(self, key: str, balance_tokens: int) -> int:
        """잔액을 직접 정한다. 운영 도구와 결제 웹훅이 쓴다."""
        self.ensure_credit(key)
        self._conn.execute(
            "UPDATE credits SET balance_tokens = ? WHERE key = ?",
            (max(0, int(balance_tokens)), key),
        )
        return self.balance(key)

    def increment_credits(self, key: str, amount: int, *, reload: bool = True) -> int:
        """크레딧을 넣는다. last_reload_at을 함께 갱신한다."""
        if amount < 0:
            raise ValueError("amount는 음수가 될 수 없다")
        self.ensure_credit(key)
        stamp = _now() if reload else None
        self._conn.execute(
            "UPDATE credits "
            "SET balance_tokens = balance_tokens + ?, "
            "    last_reload_at = COALESCE(?, last_reload_at) "
            "WHERE key = ?",
            (int(amount), stamp, key),
        )
        return self.balance(key)

    def decrement_credits(self, key: str, amount: int) -> int:
        """크레딧을 뺀다. 음수가 될 수 없다.

        BEGIN IMMEDIATE로 잠가서 동시 차감이 같은 잔액을 두 번 쓰지 않게 한다.
        부족하면 InsufficientCredits를 던진다 — ValueError의 서브클래스라
        기존 예외 처리도 깨지지 않는다. 호출 전에 balance를 확인하거나
        quota.check_credits()를 먼저 거칠 것.
        """
        if amount < 0:
            raise ValueError("amount는 음수가 될 수 없다")
        self.ensure_credit(key)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT balance_tokens FROM credits WHERE key = ?", (key,)
            ).fetchone()
            current = int(row["balance_tokens"]) if row is not None else 0
            if current < amount:
                # 롤백은 아래 except가 한다. 여기서 또 ROLLBACK을 걸면
                # "no transaction is active"로 터진다.
                raise InsufficientCredits(key, current, int(amount))
            self._conn.execute(
                "UPDATE credits SET balance_tokens = balance_tokens - ? WHERE key = ?",
                (int(amount), key),
            )
            remaining = self._conn.execute(
                "SELECT balance_tokens FROM credits WHERE key = ?", (key,)
            ).fetchone()["balance_tokens"]
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return int(remaining)

    def set_auto_reload(
        self,
        key: str,
        enabled: bool,
        amount: int | None = None,
    ) -> CreditRow:
        """자동 충전을 켜거나 끈다. amount를 주지 않으면 기본 충전액을 쓴다."""
        if amount is not None and amount < 0:
            raise ValueError("amount는 음수가 될 수 없다")
        self.ensure_credit(key)
        self._conn.execute(
            "UPDATE credits "
            "SET auto_reload = ?, auto_reload_amount = COALESCE(?, auto_reload_amount) "
            "WHERE key = ?",
            (1 if enabled else 0, amount, key),
        )
        row = self.get_credit(key)
        assert row is not None
        return row

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _to_row(row: sqlite3.Row) -> CreditRow:
        return CreditRow(
            key=row["key"],
            balance_tokens=int(row["balance_tokens"]),
            last_reload_at=row["last_reload_at"],
            auto_reload=bool(row["auto_reload"]),
            auto_reload_amount=int(row["auto_reload_amount"]),
        )
