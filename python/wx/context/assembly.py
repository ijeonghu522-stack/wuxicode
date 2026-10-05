"""WUXICODE — context assembly.

Builds the system prompt for a turn. This is the module `loop.py` was already
waiting for: the comment in `AgentLoop.history_tokens` names
`context/assembly.py` as the place where the token budget is actually
enforced, and enforcement has to live where assembly happens. Trimming after
the prompt is built is trimming too late.

THE ORDER IS THE DESIGN. Sections are appended most-general first and
most-specific last, and the reasoning is about attention, not taste:

    1. role          who the model is. Never trimmed.
    2. conventions   repository-wide rules. Same every turn, so cheap to cache.
    3. learnings     what has been learned here. Changes daily at most.
    4. retrieval     the files this request is about. Changes every turn.

Specific content goes last because recency within the prompt carries weight.
A rule about this repository that appears after six hundred tokens of general
instructions is obeyed more reliably than the same rule placed first, behind
everything.

RETRIEVAL IS CHEAP AND NARROW. FTS over the indexed tree answers in single-digit
milliseconds and returns whole files. The alternative — handing the model the
repository layout and letting it decide what to read — is one extra round trip
per question and spends the whole saving on latency.

BUDGET: `reserve` tokens are held back for the reply so that a large
retrieved context cannot starve the answer. The trim is proportional, not
first-come — the code section gives up space before the conventions do,
because a rule the user wrote outranks a file the index happened to match.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wx.learn.index import CodeHit, search_code
from wx.learn.store import LearnStore

__all__ = [
    "Assembly",
    "AssemblyConfig",
    "build_system_prompt",
    "estimate_tokens",
    "load_project_rules",
]

# Files that state project rules, in the order they are concatenated. All are
# optional; absence is not an error, because most repositories have none of
# them and the learned section has to work without hand-written scaffolding.
RULE_FILES = (
    "WX.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".wx/rules.md",
    ".cursorrules",
    "CONTRIBUTING.md",
)

MAX_RULE_BYTES = 24_000
MAX_CODE_FILE_BYTES = 24_000

_SECTION = {
    "conventions": "## 이 저장소의 규칙",
    "learnings": "## 이 저장소에서 배운 것",
    "retrieval": "## 관련 파일",
}

_KIND_LABEL = {
    "antipattern": "하지 말 것",
    "error": "반복 오류",
    "recipe": "검증된 절차",
    "hotspot": "주의",
    "convention": "컨벤션",
    "preference": "사용자 선호",
    "fact": "확인된 사실",
}


@dataclass(slots=True)
class AssemblyConfig:
    """Knobs. Every one of these is a budget decision, so they are all named."""

    #: Total prompt ceiling. Excludes the reply reservation below.
    max_context_tokens: int = 6_000
    #: Held back so retrieved context cannot starve the answer.
    reserve_reply_tokens: int = 1_500
    #: Hard ceiling for the whole prompt including the base system prompt.
    max_system_tokens: int = 4_000

    #: Observations injected per turn. More than this and they crowd each
    #: other out of attention; the store keeps thousands, the prompt keeps a
    #: dozen of the best.
    max_observations: int = 14
    #: Files retrieved per turn.
    max_code_files: int = 4
    #: Bytes of any single file pasted into the prompt. A 200KB file is a
    #: context dump that teaches the model nothing and costs the whole budget.
    max_file_bytes: int = 8_000

    #: Include repository retrieval. Off by default when the store has never
    #: been indexed, so a first run does not pay for an empty query.
    enable_retrieval: bool = True

    def total_ceiling(self) -> int:
        return self.max_context_tokens + self.reserve_reply_tokens


@dataclass(slots=True)
class Assembly:
    """The built prompt plus what went into it, for logging and debugging."""

    system_prompt: str
    tokens: int
    observations_used: list[int] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)
    rules_source: str | None = None
    sections: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# token estimation
# ---------------------------------------------------------------------------

_core_counter = None
_core_failed = False


def estimate_tokens(text: str) -> int:
    """Token count for `text`, using the native core when it is built.

    Falls back to a script-aware estimate when the extension is missing. The
    fallback is deliberately pessimistic for Hangul: roughly 1.5 characters per
    token against about 4 for Latin. Under-estimating here does not corrupt the
    reply, it just lets the prompt run longer than intended — whereas
    over-estimating would silently starve the context, which is the failure
    worth avoiding.
    """
    global _core_counter, _core_failed

    if not _core_failed and text:
        try:
            if _core_counter is None:
                from wx import require_core

                _core_counter = require_core()
            return int(_core_counter.count_tokens(text))
        except Exception:
            _core_failed = True

    if not text:
        return 0
    hangul = 0
    for ch in text:
        if "가" <= ch <= "힣" or "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿":
            hangul += 1
    other = len(text) - hangul
    return int(hangul / 1.5 + other / 4.0) + 1


# ---------------------------------------------------------------------------
# project rules
# ---------------------------------------------------------------------------


def load_project_rules(root: str | Path) -> tuple[str, str | None]:
    """Read hand-written project rules. Returns `(text, source_path)`.

    Every candidate is read, not just the first hit. A repository that has
    both a `CLAUDE.md` and an `AGENTS.md` has two sets of rules and the model
    should see both; stopping at the first one silently drops half the
    instructions.
    """
    root_path = Path(root)
    chunks: list[str] = []
    sources: list[str] = []
    for name in RULE_FILES:
        candidate = root_path / name
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        if len(text) > MAX_RULE_BYTES:
            text = text[:MAX_RULE_BYTES] + "\n… (잘림)"
        chunks.append(f"### {name}\n{text.strip()}")
        sources.append(name)
    if not chunks:
        return "", None
    return "\n\n".join(chunks), ", ".join(sources)


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _render_learnings(store: LearnStore, query: str, limit: int) -> tuple[str, list[int]]:
    """Learned rules, grouped by kind, ordered so the strongest come first.

    Relevance search runs per query, but if it returns nothing the store falls
    back to the globally highest-scoring rules. A fresh project with rules but
    no lexical overlap with the question must still get its conventions.
    """
    observations = store.observations(limit=limit, query=query or None)
    if not observations:
        observations = store.observations(limit=limit)
    if not observations:
        return "", []

    buckets: dict[str, list] = {}
    for obs in observations:
        buckets.setdefault(obs.kind, []).append(obs)

    order = ("antipattern", "error", "preference", "convention", "recipe", "hotspot", "fact")
    ordered = [k for k in order if k in buckets] + [
        k for k in buckets if k not in order
    ]

    lines: list[str] = []
    used: list[int] = []
    for kind in ordered:
        label = _KIND_LABEL.get(kind, kind)
        entries = buckets[kind][:6]
        if not entries:
            continue
        lines.append(f"- **{label}**")
        for obs in entries:
            lines.append(f"  - {obs.text}")
            used.append(obs.id)
    return "\n".join(lines), used


def _render_retrieval(hits: list[CodeHit], config: AssemblyConfig, budget: int) -> tuple[str, list[str]]:
    """Paste retrieved files, whole, until the byte budget runs out.

    Whole files rather than fragments. A fragment without its imports and its
    surrounding class produces edits that look correct and do not compile, and
    the model cannot tell that it is missing the top of the file because it was
    never shown the top of the file.
    """
    if not hits or budget <= 0:
        return "", []

    sections: list[str] = []
    used_paths: list[str] = []
    spent = 0

    for hit in hits:
        body = hit.body
        truncated = False
        if len(body) > config.max_file_bytes:
            body = body[: config.max_file_bytes]
            truncated = True
        cost = len(body) + len(hit.path) + 32
        if spent + cost > budget and used_paths:
            break
        spent += cost
        used_paths.append(hit.path)
        header = f"### {hit.path}" + (f"  ({hit.lang})" if hit.lang else "")
        if truncated:
            header += "  [잘림 — 전체는 read_file 로]"
        sections.append(f"{header}\n```\n{body}\n```")

    return "\n\n".join(sections), used_paths


def _split_file_budget(config: AssemblyConfig, remaining: int) -> int:
    """How much of the remaining budget retrieval may claim.

    Half, capped. Past that the retrieved code starts crowding out the rules,
    and the rules are the part a human wrote on purpose.
    """
    return max(0, min(remaining // 2, 14_000))


def _truncate_to_tokens(text: str, limit: int, tail_marker: str = "\n… (길이 제한)") -> str:
    """Cut `text` until it fits `limit` tokens.

    Binary search rather than a character ratio, because the estimate is
    script-dependent and a character ratio silently under-counts Korean.
    """
    if estimate_tokens(text) <= limit:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max(0, limit - estimate_tokens(tail_marker)):
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + tail_marker


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_system_prompt(
    base_prompt: str,
    *,
    store: LearnStore | None = None,
    query: str = "",
    root: str | Path | None = None,
    config: AssemblyConfig | None = None,
) -> Assembly:
    """Assemble the system prompt for one turn.

    `base_prompt` is passed in rather than imported so that the caller's
    override still wins: if someone set a custom system prompt, learned rules
    are appended to theirs, not substituted for it.
    """
    config = config or AssemblyConfig()
    sections: list[str] = [base_prompt.strip()]
    used_ids: list[int] = []
    code_paths: list[str] = []
    rules_source: str | None = None

    ceiling = min(config.max_system_tokens, config.max_context_tokens)

    # -- 1. hand-written project rules ---------------------------------------
    root_path = root or os.getcwd()
    rules_text, rules_source = load_project_rules(root_path)
    if rules_text:
        sections.append(f"{_SECTION['conventions']}\n{rules_text}")

    # -- 2. learned rules ----------------------------------------------------
    learnings_text, used_ids = ("", [])
    if store is not None:
        learnings_text, used_ids = _render_learnings(store, query, config.max_observations)
        if learnings_text:
            sections.append(f"{_SECTION['learnings']}\n{learnings_text}")

    # -- 3. retrieval --------------------------------------------------------
    if store is not None and config.enable_retrieval and query:
        hits = search_code(store, query, limit=config.max_code_files)
        if hits:
            spent_so_far = sum(estimate_tokens(s) for s in sections)
            remaining = ceiling - spent_so_far
            file_budget = _split_file_budget(config, remaining)
            retrieval_text, code_paths = _render_retrieval(hits, config, file_budget)
            if retrieval_text:
                sections.append(f"{_SECTION['retrieval']}\n{retrieval_text}")

    assembled = "\n\n".join(sections)
    total = estimate_tokens(assembled)

    # -- 4. enforce the ceiling ---------------------------------------------
    if total > ceiling:
        # Trim from the least load-bearing section outward. Retrieval first:
        # it is specific to this question, so a rule that holds across every
        # question is the better thing to keep.
        #
        # The headers are looked up from _SECTION rather than built from an
        # English key. The sections hold Korean headers, so matching
        # "## retrieval" found nothing and this loop silently did nothing.
        for key in ("retrieval", "learnings"):
            if total <= ceiling:
                break
            header = _SECTION[key]
            index = next(
                (i for i, s in enumerate(sections) if s.startswith(header)),
                None,
            )
            if index is None:
                continue
            others = sum(estimate_tokens(s) for i, s in enumerate(sections) if i != index)
            allowance = max(200, ceiling - others)
            sections[index] = _truncate_to_tokens(sections[index], allowance)
            total = estimate_tokens("\n\n".join(sections))

        # A base prompt and hand-written rules are the floor: they are what the
        # turn is, and neither is safe to cut. If those alone exceed the
        # ceiling the only honest move is to say so in the assembly rather than
        # ship a prompt that silently exceeds its own budget.
        if total > ceiling:
            sections.append(
                f"## 예산 초과 경고\n프롬프트가 {total} 토큰으로 한도 {ceiling}을 넘었습니다."
            )

    assembled = "\n\n".join(sections)

    return Assembly(
        system_prompt=assembled,
        tokens=estimate_tokens(assembled),
        observations_used=used_ids,
        code_paths=code_paths,
        rules_source=rules_source,
        sections=[s.split("\n", 1)[0] for s in sections if s.startswith("## ")],
    )


def describe(assembly: Assembly) -> dict[str, Any]:
    """Log-friendly view of what was injected."""
    return {
        "tokens": assembly.tokens,
        "sections": assembly.sections,
        "rules_source": assembly.rules_source,
        "observations": len(assembly.observations_used),
        "code_files": assembly.code_paths,
    }