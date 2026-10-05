"""WUXICODE — pattern extraction.

Turns a week of tool calls into a handful of rules. Runs nightly, costs
nothing, needs no model, and gives the same answer on the same data — which
matters more here than sophistication, because a rule that changes wording
every night is a rule the user cannot read, trust, or delete.

EXTRACTION IS STATISTICAL, NOT INFERENTIAL. Nothing here asks a model what it
thinks it learned. Every observation is a count over recorded tool calls, so
each line in the injected prompt can be traced back to the rows that produced
it. When a user asks "why does it think that", the answer is a SQL query,
not a guess.

FOUR SIGNALS, in descending order of how much they are trusted:

    antipattern  the same call failed twice or more
    error        the same normalised error text appeared three times or more
    recipe       the same call succeeded three times or more — a validated
                 procedure, worth naming so the agent reaches for it directly
    hotspot      a file edited five times or more in the window, surfaced as
                 "this file is under active change, re-read before editing"

ERROR NORMALISATION: an error message with a line number in it is a different
string every time it happens, so raw grouping finds nothing. Numbers, hex
addresses, quoted paths, UUIDs and durations collapse to placeholders before
grouping. The first line is kept because that is where the exception type
lives, and it is the part a human recognises.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from wx.learn.store import LearnStore

__all__ = ["ExtractionResult", "extract", "normalise_error"]

# Order matters when rendering the injected prompt: the section labels read
# top-down and the model weights the top of the prompt most heavily.
SECTION_ORDER = ("antipattern", "error", "recipe", "hotspot", "convention", "preference", "fact")


@dataclass(slots=True)
class ExtractionResult:
    """What one extraction pass produced."""

    observations: list[str]
    added: int = 0
    scanned_calls: int = 0
    window_days: float = 7.0

    @property
    def empty(self) -> bool:
        return self.added == 0


# Ordered alternation — first match wins, so the UUID rule must precede the
# plain-hex rule or it would eat the hex address and never reach its own
# branch.
_SUB_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<uuid>"),
    (r"0x[0-9a-fA-F]+", "<hex>"),
    (r"\b\d{4}-\d{2}-\d{2}[T ]\d+:\d+:\d+\S*", "<time>"),
    (r"\b[0-9a-f]{7,40}\b", "<hash>"),
    (r"[A-Za-z]:\\[^\s'\"]+", "<path>"),
    (r"(?:/[\w.\-]+){2,}", "<path>"),
    (r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds|minutes|m|MiB|GiB|KB|MB|GB)\b", "<dur>"),
    (r"\bline \d+\b", "line <n>"),
    (r":\d+:\d+", ":<n>"),
    (r"\b\d+\b", "<n>"),
)

# Every pattern gets a NAMED outer group, and any group inside a pattern is made
# non-capturing. normalise_error maps a match back to its replacement by group
# name, so an unnamed inner group would shift every later group index and a
# nested capture would make the name lookup ambiguous.
_SUB_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, (p, _) in enumerate(_SUB_PATTERNS, 1)))

_ERROR_HEADS = (
    "Traceback", "error", "fatal", "exception", "failed", "cannot", "could not",
    "no such", "permission denied", "undefined", "not found", "assertionerror",
)


def normalise_error(text: str) -> str:
    """Collapse a raw error to a stable key.

    Keeps the first meaningful line and replaces every varying token. Without
    the head-line truncation a multi-line traceback contributes its own frame
    list to the key, and two occurrences of the same bug hash differently.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = ""
    for line in lines[:4]:
        if any(line.lower().startswith(h) or h in line.lower() for h in _ERROR_HEADS):
            head = line
            break
    if not head:
        head = lines[0] if lines else ""
    if not head:
        return ""
    head = head[:180]

    named = {f"g{i}": repl for i, (_, repl) in enumerate(_SUB_PATTERNS, start=1)}
    return _SUB_RE.sub(lambda m: named.get(m.lastgroup or "", m.group(0)), head).strip()


def _truncate_args(args: str, limit: int = 120) -> str:
    one_line = " ".join(args.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _looks_like_language_convention(args: str) -> str | None:
    """Infer a comment-language convention from an edit payload.

    Deliberately narrow. It looks only at comment markers and counts the
    script each comment is written in, and it only fires when the evidence is
    lopsided — a file with two Korean and twenty English comments says nothing.
    A wrong convention injected into every future prompt is expensive; a missed
    one costs nothing.
    """
    if "//" not in args and "#" not in args and "/*" not in args:
        return None
    hangul = sum(1 for ch in args if "가" <= ch <= "힣")
    latin = sum(1 for ch in args if ("a" <= ch.lower() <= "z"))
    if hangul >= 12 and hangul > latin * 2:
        return "이 저장소는 한국어 주석을 사용한다. 새 코드에도 한국어 주석을 유지한다."
    if latin >= 40 and latin > hangul * 4:
        return "이 저장소의 주석은 영어다. 새 코드에도 영어 주석을 유지한다."
    return None


def extract(
    store: LearnStore,
    *,
    window_days: float = 7.0,
    now: float | None = None,
) -> ExtractionResult:
    """Run every extractor over the window and persist what it finds.

    Idempotent — `add_observation` treats (kind, text) as unique and refreshes
    the timestamp of a repeat rather than inserting a duplicate, so running
    this twice an hour produces the same prompt as running it once a day.
    """
    import time as _time

    now = now if now is not None else _time.time()
    candidates: list[tuple[str, str]] = []

    calls = store.tool_calls_since(now - window_days * 86400.0, limit=2000)
    if not calls:
        return ExtractionResult(added=0, observations=[], scanned_calls=0, window_days=window_days)

    # -- antipatterns: same call, same arguments, failed twice ---------------
    fail_counter: Counter[tuple[str, str]] = Counter()
    for call in calls:
        if not call.ok:
            fail_counter[(call.name, _truncate_args(call.args))] += 1
    for (name, args), n in fail_counter.most_common(20):
        if n >= 2:
            candidates.append(
                ("antipattern", f"`{name}` 를 다음 인자로 {n}회 실패했다 — 반복하지 마라: {args}")
            )

    # -- error signatures ----------------------------------------------------
    err_counter: Counter[str] = Counter()
    for call in calls:
        if call.ok or not call.result:
            continue
        key = normalise_error(call.result)
        if key:
            err_counter[key] += 1
    for key, n in err_counter.most_common(15):
        if n >= 3:
            candidates.append(("error", f"이 저장소에서 {n}회 반복된 오류 — 먼저 확인할 것: {key}"))

    # -- recipes: a procedure that kept working ------------------------------
    ok_counter: Counter[tuple[str, str]] = Counter()
    for call in calls:
        if call.ok:
            ok_counter[(call.name, _truncate_args(call.args))] += 1
    for (name, args), n in ok_counter.most_common(20):
        if n >= 3:
            candidates.append(("recipe", f"검증된 절차 — `{name} {args}` 는 {n}회 성공했다."))

    # -- hotspots: files under active churn ---------------------------------
    edited: Counter[str] = Counter()
    for call in calls:
        if call.name not in ("write_file", "edit_file") or not call.ok:
            continue
        path = _extract_path(call.args)
        if path:
            edited[path] += 1
    for path, n in edited.most_common(10):
        if n >= 5:
            candidates.append(
                ("hotspot", f"`{path}` 는 최근 {n}회 수정됨 — 편집 전 반드시 다시 읽을 것.")
            )

    # -- language convention -------------------------------------------------
    language_votes: Counter[str] = Counter()
    for call in calls:
        if call.name in ("write_file", "edit_file") and call.ok:
            vote = _looks_like_language_convention(call.args)
            if vote:
                language_votes[vote] += 1
    for vote, n in language_votes.most_common(1):
        if n >= 2:
            candidates.append(("convention", vote))

    source = f"extract:{now:.0f}"
    texts = [text for _, text in candidates]
    store.add_observations(candidates, source)

    return ExtractionResult(
        added=len(texts),
        observations=texts,
        scanned_calls=len(calls),
        window_days=window_days,
    )


def _extract_path(args: str) -> str | None:
    """Pull a path out of a serialised argument blob."""
    import json

    try:
        data = json.loads(args)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict):
        value = data.get("path") or data.get("file_path") or data.get("filename")
        if isinstance(value, str) and value:
            return value
    return None


def grouped_by_kind(store: LearnStore, limit_per_kind: int = 12) -> dict[str, list]:
    """Current observations bucketed by kind, in prompt order."""
    out: dict[str, list] = {}
    for kind in SECTION_ORDER:
        rows = store.observations(kind=kind, limit=limit_per_kind)
        if rows:
            out[kind] = rows
    # Anything with a kind outside the known set still gets shown, last, so an
    # observation written by `capture.note` with a novel kind is never silently
    # dropped from the prompt.
    for obs in store.observations(limit=100):
        if obs.kind not in SECTION_ORDER and obs.kind not in out:
            out.setdefault("fact", []).append(obs)
    return out


def diff_of_edits(store: LearnStore, window_days: float = 7.0) -> dict[str, int]:
    """Per-file edit counts. The digest renders this as a work summary."""
    import json
    import time as _time

    counter: Counter[str] = Counter()
    by_name: dict[str, Counter] = defaultdict(Counter)
    for call in store.tool_calls_since(_time.time() - window_days * 86400.0, limit=2000):
        by_name[call.name][call.ts] += 1
        if call.name in ("write_file", "edit_file") and call.ok:
            try:
                data = json.loads(call.args)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                path = data.get("path")
                if isinstance(path, str):
                    counter[path] += 1
    return dict(counter.most_common(30))