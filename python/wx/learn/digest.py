"""WUXICODE — daily digest.

The nightly job. Four steps, in this order:

    1. extract   mine the day's tool calls for rules
    2. report    render them for a human
    3. publish   write them to a markdown file the user can read and edit
    4. prune     drop observations past the horizon

Step 3 is the one that makes the rest trustworthy. A learning system whose
memory is only visible as a change in model behaviour is one nobody can audit,
and an unauditable prompt injection is indistinguishable from a backdoor. The
published file is the whole memory in plain text, and when the agent starts
asserting something the file does not contain, that is a bug with a visible
diff rather than a mystery.

THE FILE IS NOT OVERWRITTEN. Human edits — a rule added by hand, a line
reworded — live between markers, and regeneration replaces only the generated
section. Overwriting user notes would train people to never read the file,
which loses the entire audit property.

SCHEDULING IS NOT THIS MODULE'S JOB. It exposes `run_digest` and returns a
report; whether that runs from cron, a Task Scheduler entry, or the CLI is the
deployment's problem. What it does own is idempotence — running twice produces
the same file, so a retry after a partial failure is safe.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wx.learn.extract import ExtractionResult, extract, grouped_by_kind
from wx.learn.index import index_stats
from wx.learn.store import LearnStore, default_store_path

__all__ = ["DigestReport", "run_digest", "rules_path", "publish", "render_report"]

#: Human edits outside these markers survive regeneration.
BEGIN_MARKER = "<!-- WX-LEARNED:BEGIN -->"
END_MARKER = "<!-- WX-LEARNED:END -->"

_KIND_TITLE = {
    "antipattern": "하지 말 것",
    "error": "반복 오류",
    "recipe": "검증된 절차",
    "hotspot": "주의할 파일",
    "convention": "컨벤션",
    "preference": "사용자 선호",
    "fact": "확인된 사실",
}

_KIND_ORDER = ("antipattern", "error", "preference", "convention", "recipe", "hotspot", "fact")


@dataclass(slots=True)
class DigestReport:
    """Everything one digest run produced."""

    started: float
    added: int = 0
    pruned: int = 0
    scanned_calls: int = 0
    window_days: float = 7.0
    written_to: str | None = None
    report_text: str = ""
    notes: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "pruned": self.pruned,
            "scanned_calls": self.scanned_calls,
            "window_days": self.window_days,
            "written_to": self.written_to,
            "notes": self.notes,
            "stats": self.stats,
            "elapsed_s": round(self.elapsed, 3),
        }


def rules_path() -> Path:
    """Where the published rules file lives.

    Per-user state, next to the store — not inside any repository. A rules file
    that travels with a checkout would be committed, and a commit of one
    developer's learned antipatterns is a leak of that developer's mistakes to
    everyone else.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "wuxicode" / "learned-rules.md"


def _render_section(store: LearnStore, generated_at: float, window_days: float) -> str:
    """The generated block: current rules, grouped by kind."""
    grouped = grouped_by_kind(store)
    lines = [
        BEGIN_MARKER,
        f"<!-- 갱신: {time.strftime('%Y-%m-%d %H:%M:%S')} · 최근 {window_days:.0f}일 -->",
        "",
    ]

    if not grouped:
        lines.append("_아직 기록된 규칙이 없습니다. 툴을 실제로 사용하면 여기부터 채워집니다._")
        lines.append("")
        lines.append(END_MARKER)
        return "\n".join(lines)

    for kind in _KIND_ORDER:
        obs = grouped.get(kind)
        if not obs:
            continue
        title = _KIND_TITLE.get(kind, kind)
        lines.append(f"### {title}")
        for item in obs:
            age = f"{item.age_days:.0f}일 전" if item.age_days >= 1 else "오늘"
            used = f" · 사용 {item.hits}회" if item.hits else ""
            lines.append(f"- {item.text}  <sub>{age}{used}</sub>")
        lines.append("")

    leftovers = [k for k in grouped if k not in _KIND_ORDER]
    if leftovers:
        lines.append("### 기타")
        for kind in leftovers:
            for item in grouped[kind]:
                lines.append(f"- [{kind}] {item.text}")
        lines.append("")

    lines.append(END_MARKER)
    return "\n".join(lines)


def publish(store: LearnStore, window_days: float = 7.0, path: Path | None = None) -> str:
    """Write the rules file, preserving anything outside the markers.

    A file that exists but has lost its markers — hand-edited into a shape the
    generator does not recognise — is not overwritten. The text is written to a
    `.new` sibling and the collision is reported instead, because silently
    replacing a user's unrecognised file is how data disappears.
    """
    target = Path(path) if path else rules_path()
    generated = _render_section(store, time.time(), window_days)

    header = (
        "# WX 학습 규칙\n\n"
        "이 파일은 `wx learn digest` 가 자동 생성합니다.\n"
        f"{BEGIN_MARKER} … {END_MARKER} 사이만 갱신되고, 그 밖에 적은 내용은 유지됩니다.\n"
        "직접 추가한 규칙은 마커 바깥에 쓰세요.\n\n"
    )

    existing = ""
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            existing = ""

    if existing:
        start = existing.find(BEGIN_MARKER)
        end = existing.find(END_MARKER)
        if start == -1 or end == -1 or end < start:
            backup = target.with_suffix(target.suffix + ".new")
            backup.write_text(header + generated + "\n", encoding="utf-8")
            raise ValueError(
                f"기존 파일에 마커가 없어 덮어쓰지 않습니다: {target}\n"
                f"새 버전은 {backup} 에 기록했습니다."
            )
        merged = existing[:start] + generated + existing[end + len(END_MARKER):]
    else:
        merged = header + generated + "\n"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(merged, encoding="utf-8")
    return str(target)


def render_report(store: LearnStore, extraction: ExtractionResult, path: str | None) -> str:
    """Terminal-facing summary of a digest run."""
    stats = store.stats()
    corpus = index_stats(store)

    lines = [
        "",
        "=" * 60,
        "  WX 학습 다이제스트",
        "=" * 60,
        f"  기간          : 최근 {extraction.window_days:.0f}일",
        f"  분석한 툴콜   : {extraction.scanned_calls}건",
        f"  새 규칙       : {extraction.added}건",
        f"  저장소        : {stats.get('path', '?')}",
        f"  토크나이저     : {stats.get('tokenizer', '?')}",
        f"  인덱스 파일    : {corpus.get('files', 0)}개 ({corpus.get('bytes', 0) / 1024:.0f} KB)",
        f"  누적 규칙     : {stats.get('observations', 0)}건",
        f"  누적 턴       : {stats.get('turns', 0)}건",
        f"  저장소 크기   : {stats.get('size_bytes', 0) / 1024 / 1024:.2f} MB",
    ]
    if path:
        lines.append(f"  규칙 파일     : {path}")
    lines.append("=" * 60)

    if extraction.observations:
        lines.append("")
        lines.append("  이번에 배운 것")
        lines.append("  " + "-" * 56)
        for text in extraction.observations[:20]:
            lines.append(f"  · {text}")
        if len(extraction.observations) > 20:
            lines.append(f"  … 외 {len(extraction.observations) - 20}건")
    else:
        lines.append("")
        lines.append("  새 규칙 없음. 툴 사용 기록이 더 쌓이면 여기서 배웁니다.")

    lines.append("")
    return "\n".join(lines)


def run_digest(
    store: LearnStore | None = None,
    *,
    window_days: float = 7.0,
    publish_rules: bool = True,
    prune_days: float = 180.0,
    path: Path | None = None,
) -> DigestReport:
    """Extract, report, publish, prune.

    Each step is independently guarded. A prune failure must not lose the
    extraction that just succeeded, so the steps do not share a try block.
    """
    report = DigestReport(started=time.time(), window_days=window_days)
    store = store or LearnStore(default_store_path())

    try:
        extraction = extract(store, window_days=window_days)
        report.added = extraction.added
        report.scanned_calls = extraction.scanned_calls
    except Exception as exc:
        report.notes.append(f"추출 실패: {exc}")
        extraction = ExtractionResult(added=0, observations=[], scanned_calls=0)

    if publish_rules:
        try:
            report.written_to = publish(store, window_days, path)
        except ValueError as exc:
            report.notes.append(str(exc))
        except Exception as exc:
            report.notes.append(f"규칙 파일 쓰기 실패: {exc}")

    try:
        report.pruned = store.prune(prune_days)
    except Exception as exc:
        report.notes.append(f"정리 실패: {exc}")

    try:
        report.stats = store.stats()
    except Exception:
        pass

    report.report_text = render_report(store, extraction, report.written_to)
    if report.notes:
        report.report_text += "\n  경고\n  " + "-" * 56 + "\n"
        for note in report.notes:
            report.report_text += f"  ! {note}\n"
        report.report_text += "\n"

    return report