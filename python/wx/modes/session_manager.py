"""WUXICODE — 세션 관리.

세션 = 이름 붙은 대화 하나. 에이전트 모드에서 "프로젝트 A", "프로젝트 B"
를 오가려면 대화가 분리돼 있어야 하고, 그 대화를 다시 찾으려면 이름이
있어야 한다. Claude Code / Codex 계열의 세션 탭이 하는 일을 라인 단위
출력으로 옮긴 버전이다.

티어 판정이 여기서 일어난다. FREE는 동시 세션 1개, 유료 티어는 좌석 수만큼.
새 세션을 만들 수 있는지는 tiers.py의 can_multi_session와 max_sessions가
결정한다.

티어 원천을 어디서 읽는가:

    1. license_server.tiers — 저장소 안에서 실행할 때(개발/서버)
    2. ~/.wuxicode/tiers.json — 라이선스 서버가 남긴 스냅샷(설치본)
    3. 없으면 FREE — 판정에 실패하면 가장 좁은 티어로 떨어진다

3번이 방향인 이유: 알 수 없는 상태에서 무료 제한을 먼저 적용하면
사용자에게 보이는 메시지가 "업그레이드 안내"이고, 반대로 넉넉하게 열면
티어 제한이 우회된다. 실패할 때는 조여야 한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# pyproject promises 3.10; datetime.UTC only exists from 3.11. timezone.utc is
# the same object and works everywhere.
UTC = timezone.utc

__all__ = [
    "Session",
    "SessionError",
    "SessionLimitError",
    "SessionManager",
    "TierView",
    "current_tier",
    "tiers_snapshot_path",
]

UPGRADE_URL = "https://wuxicode.dev/pricing"
DEFAULT_SESSION_NAME = "main"


@dataclass(frozen=True, slots=True)
class TierView:
    """클라이언트가 티어에 대해 아는 값.

    license_server의 TierConfig에서 필요한 필드만 옮긴 본이다. 서버가
    서명한 토큰으로 대체되기 전까지의 임시 뷰다.
    """

    key: str
    name: str
    can_multi_session: bool
    max_sessions: int
    max_seats: int
    max_concurrent: int
    daily_tokens: int

    @property
    def short_name(self) -> str:
        """상태 줄에 쓰는 짧은 이름."""
        return {
            "free": "무료",
            "plus": "PLUS",
            "zen": "ZEN",
            "ultimate": "ULTIMATE",
        }.get(self.key, self.name)


# FREE 값. tiers.py를 못 읽을 때의 기본값 — 가장 좁은 권리로 시작한다.
_FREE = TierView(
    key="free",
    name="WX",
    can_multi_session=False,
    max_sessions=3,
    max_seats=1,
    max_concurrent=1,
    daily_tokens=50_000,
)


@dataclass(slots=True)
class Session:
    """대화 하나."""

    name: str
    messages: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tokens_used: int = 0

    def rename(self, new_name: str) -> None:
        self.name = new_name.strip() or self.name


class SessionError(Exception):
    """세션 조작 실패."""


class SessionLimitError(SessionError):
    """티어 한도에 걸려 세션을 만들지 못함.

    사용자에게 그대로 보여줄 문장을 message로 들고 있다.
    """

    def __init__(self, message: str, lines: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.lines = lines or [message]


def tiers_snapshot_path() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = Path(home) if home else Path.cwd()
    return base / ".wuxicode" / "tiers.json"


def _view_from_mapping(raw: dict) -> TierView:
    """tiers.py의 TIERS 항목 또는 JSON 스냅샷 한 건을 TierView로."""

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return TierView(
        key=str(raw.get("key") or raw.get("tier") or "free").lower(),
        name=str(raw.get("name") or "WX"),
        can_multi_session=bool(raw.get("can_multi_session", False)),
        max_sessions=_int("max_sessions", 3),
        max_seats=_int("max_seats", 1),
        max_concurrent=_int("max_concurrent", 1),
        daily_tokens=_int("daily_tokens", 50_000),
    )


def _load_tiers() -> dict[str, dict]:
    """tiers.py 또는 스냅샷에서 티어 테이블을 읽는다. 실패하면 빈 dict."""
    # 1. 저장소에서 직접
    try:
        from license_server.tiers import TIERS

        return {
            tier.value: {
                "key": tier.value,
                "name": cfg.name,
                "can_multi_session": cfg.can_multi_session,
                "max_sessions": cfg.max_sessions,
                "max_seats": cfg.max_seats,
                "max_concurrent": cfg.max_concurrent,
                "daily_tokens": cfg.daily_tokens,
            }
            for tier, cfg in TIERS.items()
        }
    except Exception:
        pass

    # 2. 설치본 스냅샷
    path = tiers_snapshot_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    tiers = data.get("tiers") if isinstance(data, dict) else None
    if not isinstance(tiers, dict):
        return {}
    out: dict[str, dict] = {}
    for key, raw in tiers.items():
        if isinstance(raw, dict):
            out[str(key).lower()] = {**raw, "key": str(key).lower()}
    return out


def current_tier(license_key: str | None = None) -> TierView:
    """라이선스 키로부터 티어를 결정한다.

    키가 없거나 접두사를 못 읽으면 FREE. Phase 5에서 서명 토큰으로
    교체되면 여기만 바뀐다.
    """
    key = (license_key or os.environ.get("WX_LICENSE_KEY") or "").strip()
    if not key:
        return _FREE

    head, sep, _rest = key.partition("-")
    if not sep:
        return _FREE

    tiers = _load_tiers()
    if not tiers:
        return _FREE
    raw = tiers.get(head.upper().lower())
    if raw is None:
        return _FREE
    return _view_from_mapping(raw)


class SessionManager:
    """세션 목록과 현재 세션을 관리한다.

    시작할 때 main 세션 하나가 있다. FREE 티어는 여기서 더 못 만든다.
    """

    def __init__(
        self,
        tier: TierView | None = None,
        *,
        license_key: str | None = None,
    ) -> None:
        self.tier = tier or current_tier(license_key)
        self._sessions: list[Session] = [
            Session(name=DEFAULT_SESSION_NAME, tokens_used=0)
        ]
        self._active = 0

    # --- 상태 ---------------------------------------------------------------

    @property
    def current(self) -> Session:
        return self._sessions[self._active]

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions)

    @property
    def active_index(self) -> int:
        return self._active

    def names(self) -> list[str]:
        return [s.name for s in self._sessions]

    def __len__(self) -> int:
        return len(self._sessions)

    def _limit_message(self) -> list[str]:
        """FREE 티어용 업그레이드 안내."""
        return [
            "✗ 무료 티어는 동시 세션 1개만 가능.",
            f"  PLUS ($10/월)로 업그레이드: {UPGRADE_URL}",
        ]

    # --- 조작 ---------------------------------------------------------------

    def create(self, name: str = "") -> Session:
        """새 세션.

        FREE는 여기서 막힌다. 세션 하나가 이미 열려 있으므로 — 동시 실행이
        아니라 세션 자체를 못 만든다는 뜻으로, 닫고 하나만 쓰게 한다.
        """
        if not self.tier.can_multi_session:
            raise SessionLimitError(
                "무료 티어는 동시 세션 1개만 가능.", self._limit_message()
            )
        if len(self._sessions) >= self.tier.max_sessions:
            raise SessionLimitError(
                f"저장 가능한 세션 수 한도({self.tier.max_sessions})에 도달했습니다."
            )

        clean = name.strip() or self._default_name()
        if any(s.name == clean for s in self._sessions):
            clean = f"{clean}-{len(self._sessions) + 1}"

        session = Session(name=clean)
        self._sessions.append(session)
        self._active = len(self._sessions) - 1
        return session

    def _default_name(self) -> str:
        return f"session-{len(self._sessions) + 1}"

    def rename(self, new_name: str, index: int | None = None) -> Session:
        """세션 이름 변경."""
        target = self.current if index is None else self._at(index)
        clean = new_name.strip()
        if not clean:
            raise SessionError("세션 이름이 비어 있습니다.")
        if any(s.name == clean and s is not target for s in self._sessions):
            raise SessionError(f"이미 있는 이름입니다: {clean}")
        target.rename(clean)
        return target

    def switch(self, index: int) -> Session:
        """세션 전환. 1부터 시작하는 번호를 받는다."""
        target = self._at(index)
        self._active = self._sessions.index(target)
        return target

    def close(self, index: int) -> str:
        """세션을 닫는다. 남은 세션이 없으면 닫을 수 없다."""
        if len(self._sessions) == 1:
            raise SessionError("마지막 세션은 닫을 수 없습니다.")
        target = self._at(index)
        pos = self._sessions.index(target)
        name = target.name
        self._sessions.pop(pos)
        if self._active >= pos:
            self._active = max(0, self._active - 1)
        return name

    def _at(self, index: int) -> Session:
        """1-based 번호를 Session으로. 범위를 벗어나면 SessionError."""
        if not isinstance(index, int) or index < 1 or index > len(self._sessions):
            raise SessionError(
                f"세션 번호가 범위를 벗어났습니다: {index} (1~{len(self._sessions)})"
            )
        return self._sessions[index - 1]

    # --- 출력 ---------------------------------------------------------------

    def render_list(self) -> str:
        """/session list 출력."""
        if len(self._sessions) == 1:
            return f"세션 1개 (닫을 수 없음):  1. {self.current.name}"
        lines = [f"세션 {len(self._sessions)}/{self.tier.max_sessions}개:"]
        for i, session in enumerate(self._sessions, start=1):
            mark = "*" if i - 1 == self._active else " "
            lines.append(f" {mark} {i}. {session.name}")
        return "\n".join(lines)

    def tab_names(self) -> list[str]:
        return self.names()

    def state_line(self) -> str:
        """프롬프트 옆에 붙는 한 줄."""
        return (
            f"세션 {self._active + 1}/{len(self._sessions)} "
            f"({self.tier.short_name}) · {self.current.name}"
        )