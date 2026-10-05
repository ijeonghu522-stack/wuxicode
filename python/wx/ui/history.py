"""WUXICODE — 생성 파일 이력.

에이전트가 만든(또는 수정한) 파일을 ~/.wuxicode/history.json에 쌓고,
/history로 다시 꺼내 본다. 파일 내용 복사본을 남기는 게 아니라 경로와
크기와 시각만 남긴다 — 이력은 파일을 되찾아주는 목록이지 백업이 아니다.

저장 형식:

    {"files": [{"path": "snake.html", "size": 16071,
                "created_at": "2026-10-05T12:00:00+00:00"}]}

같은 경로가 다시 기록되면 항목을 하나 더 만드는 게 아니라 갱신한다.
한 세션에서 파일을 열 번 고쳐도 목록은 한 줄이어야 한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wx.ui.token_meter import format_bytes

# pyproject promises 3.10; datetime.UTC only exists from 3.11.
UTC = timezone.utc

__all__ = [
    "History",
    "HistoryEntry",
    "history_path",
    "get_history",
]

# 기록이 무한정 쌓이지 않게. 한 천 개를 넘으면 오래된 것부터 버린다.
MAX_ENTRIES = 1000

_SCOPES = ("today", "week", "all")


def history_path() -> Path:
    """~/.wuxicode/history.json. WX_HISTORY_PATH로 덮어쓸 수 있다."""
    override = os.environ.get("WX_HISTORY_PATH")
    if override:
        return Path(override).expanduser()
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = Path(home) if home else Path.cwd()
    return base / ".wuxicode" / "history.json"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    path: str
    size: int
    created_at: datetime

    @property
    def name(self) -> str:
        return Path(self.path).name

    def age_text(self, now: datetime | None = None) -> str:
        """'방금' / '5분 전' / '3시간 전' / '2일 전'."""
        delta = (now or _now()) - self.created_at
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "방금"
        if seconds < 3600:
            return f"{seconds // 60}분 전"
        if seconds < 86400:
            return f"{seconds // 3600}시간 전"
        return f"{seconds // 86400}일 전"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "created_at": self.created_at.isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "HistoryEntry":
        stamp = raw.get("created_at")
        try:
            created = datetime.fromisoformat(stamp) if stamp else _now()
        except ValueError:
            created = _now()
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return cls(
            path=str(raw.get("path", "")),
            size=int(raw.get("size", 0) or 0),
            created_at=created,
        )


class History:
    """파일 하나로 때리는 생성 이력."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else history_path()

    # --- 저장/로드 ----------------------------------------------------------

    def load(self) -> list[HistoryEntry]:
        """이력을 읽는다. 파일이 없거나 깨졌으면 빈 목록."""
        try:
            raw_text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return []
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            return []
        out: list[HistoryEntry] = []
        for item in files:
            if isinstance(item, dict) and item.get("path"):
                out.append(HistoryEntry.from_dict(item))
        return out

    def save(self, entries: list[HistoryEntry]) -> None:
        """이력을 쓴다. MAX_ENTRIES를 넘으면 오래된 것부터 버린다."""
        trimmed = entries[-MAX_ENTRIES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"files": [e.to_dict() for e in trimmed]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    # --- 기록 ---------------------------------------------------------------

    def record(
        self,
        path: str | Path,
        size: int | None = None,
        *,
        session: str | None = None,
    ) -> HistoryEntry:
        """파일을 이력에 올린다. 같은 경로는 갱신.

        session은 프롬프트(세션 이름)다. 저장 형식에 넣지 않고 호출부의
        로그에만 쓴다 — 사용자가 history.json을 열어봤을 때 필요한 정보는
        경로·크기·시각뿐이다.
        """
        target = Path(path)
        try:
            resolved = str(target.resolve())
        except (OSError, RuntimeError):
            resolved = str(target)

        if size is None:
            try:
                size = target.stat().st_size
            except OSError:
                size = 0

        entry = HistoryEntry(path=resolved, size=int(size), created_at=_now())

        entries = [e for e in self.load() if e.path != resolved]
        entries.append(entry)
        self.save(entries)
        return entry

    def clear(self) -> None:
        self.save([])

    # --- 조회 ---------------------------------------------------------------

    def entries(self, scope: str = "today") -> list[HistoryEntry]:
        """범위로 걸러 newest first."""
        key = (scope or "today").lower()
        if key not in _SCOPES:
            key = "today"
        entries = sorted(self.load(), key=lambda e: e.created_at, reverse=True)
        if key == "all":
            return entries
        days = 1 if key == "today" else 7
        cutoff = _now() - timedelta(days=days)
        return [e for e in entries if e.created_at >= cutoff]

    def find(self, query: str) -> list[HistoryEntry]:
        """경로 또는 이름에 문자열이 들어간 항목을 newest first."""
        q = (query or "").strip().lower()
        if not q:
            return []
        return [
            e
            for e in sorted(self.load(), key=lambda x: x.created_at, reverse=True)
            if q in e.path.lower()
        ]

    @staticmethod
    def total_bytes(entries: list[HistoryEntry]) -> int:
        return sum(e.size for e in entries)

    # --- 렌더링 -------------------------------------------------------------

    def render(
        self,
        scope: str = "today",
        *,
        color: bool = False,
    ) -> str:
        """/history 출력. 비었으면 빈 상태 문구를 돌려준다."""
        from wx.ui import brand

        key = (scope or "today").lower()
        if key not in _SCOPES:
            key = "today"
        label = {"today": "오늘", "week": "이번 주", "all": "전체"}[key]
        entries = self.entries(key)

        if not entries:
            return f"최근 생성 파일 ({label}): 없음"

        lines = [f"최근 생성 파일 ({label}):"]
        now = _now()
        for i, entry in enumerate(entries, start=1):
            mark = "✓ 미리보기" if entry.name.lower().endswith(
                (".html", ".htm", ".md")
            ) else "✓"
            lines.append(
                f"  [{i}] {entry.name:<24} {format_bytes(entry.size):>9}  "
                f"{entry.age_text(now):<8} {mark}"
            )
        total = self.total_bytes(entries)
        lines.append(f"총 {len(entries)}개 파일, {format_bytes(total)}")
        if color:
            lines[0] = f"{brand.fg(brand.GRADIENT_START)}{lines[0]}{brand.RESET}"
        return "\n".join(lines)

    def render_entry(self, entry: HistoryEntry) -> str:
        """특정 파일 한 건의 상세."""
        target = Path(entry.path)
        exists = "있음" if target.exists() else "없음 (삭제됨)"
        return "\n".join(
            [
                f"파일: {entry.path}",
                f"  크기: {format_bytes(entry.size)}",
                f"  기록: {entry.created_at.isoformat(timespec='seconds')}",
                f"  경과: {entry.age_text()}",
                f"  현재: {exists}",
                "",
                "/preview 로 내용을 봅니다.",
            ]
        )


_history: History | None = None


def get_history() -> History:
    """전역 이력 인스턴스."""
    global _history
    if _history is None:
        _history = History()
    return _history


def set_history(history: History) -> None:
    """전역 이력을 갈아 끼운다 (테스트용)."""
    global _history
    _history = history