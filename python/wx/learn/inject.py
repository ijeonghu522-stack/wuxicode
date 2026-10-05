"""WUXICODE — injection.

The seam between the learning store and the agent loop. One function, called
once per turn, that turns stored knowledge into a system prompt and records
that it did.

It exists as its own module so that `loop.py` does not import SQLite, does not
know observations have kinds, and does not grow a token budget. The loop asks
for a prompt and gets a prompt. Everything about how that prompt was
assembled — retrieval, ranking, trimming, feedback — stays on this side of the
call, which means none of it can break a turn.

The feedback write (`mark_used`) is the part worth explaining. Injecting a rule
and forgetting that it was injected would make the ranking static: rules would
accumulate purely by age, and a rule that stopped being relevant but kept
being recently recorded would never fall out. Bumping the counter on use means
the set that survives is the set that earned its place.

USE DISABLED BY DEFAULT. Injection has to be switchable because a store built
up over months is not always right for the machine you are on today, and a
system prompt that cannot be emptied is a system prompt you cannot escape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wx.context.assembly import Assembly, AssemblyConfig, build_system_prompt
from wx.learn.store import LearnStore, default_store_path

__all__ = ["Injector", "InjectedTurn", "inject_prompt"]


@dataclass(slots=True)
class InjectedTurn:
    """What one injection produced."""

    prompt: str
    tokens: int
    observations: int
    code_files: list[str]
    rules_source: str | None
    sections: list[str]
    elapsed_ms: float

    @property
    def empty(self) -> bool:
        return not self.observations and not self.code_files and self.rules_source is None


class Injector:
    """Builds turn prompts from the store.

    Holds the config and the workspace root so callers pass a query and get a
    prompt. The store is optional: an `Injector` with `store=None` degrades to
    returning the base prompt untouched, which is what a machine that has never
    run the learning layer should do.
    """

    def __init__(
        self,
        *,
        store: LearnStore | None = None,
        root: str | Path | None = None,
        config: AssemblyConfig | None = None,
        enabled: bool = True,
    ) -> None:
        self.store = store
        self.root = Path(root) if root else Path.cwd()
        self.config = config or AssemblyConfig()
        self.enabled = enabled
        self._last: InjectedTurn | None = None

    @property
    def last(self) -> InjectedTurn | None:
        return self._last

    def available(self) -> bool:
        return self.store is not None and self.store.healthy()

    def prompt(self, base_prompt: str, query: str = "") -> InjectedTurn:
        """Return the system prompt for `query`.

        Timing is measured because the whole claim of this feature is that the
        cost is a few hundred milliseconds. Without the number it is a promise;
        with it, a regression shows up as a number.
        """
        started = time.perf_counter()

        if not self.enabled or self.store is None:
            turn = InjectedTurn(
                prompt=base_prompt,
                tokens=0,
                observations=0,
                code_files=[],
                rules_source=None,
                sections=[],
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            self._last = turn
            return turn

        try:
            assembly: Assembly = build_system_prompt(
                base_prompt,
                store=self.store,
                query=query,
                root=self.root,
                config=self.config,
            )
        except Exception:
            # Injection is an enhancement. If it raises, the turn still runs
            # with the plain base prompt — silently, because surfacing this as
            # a user-facing error would train the user to distrust a system
            # that has nothing to apologise for.
            turn = InjectedTurn(
                prompt=base_prompt,
                tokens=0,
                observations=0,
                code_files=[],
                rules_source=None,
                sections=[],
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            self._last = turn
            return turn

        turn = InjectedTurn(
            prompt=assembly.system_prompt,
            tokens=assembly.tokens,
            observations=len(assembly.observations_used),
            code_files=assembly.code_paths,
            rules_source=assembly.rules_source,
            sections=assembly.sections,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._last = turn

        try:
            self.store.mark_used(assembly.observations_used)
        except Exception:
            pass

        return turn

    def explain(self) -> str:
        """Human-readable dump of the last injection. For `/wx why`."""
        turn = self._last
        if turn is None:
            return "아직 주입된 적이 없습니다."
        lines = [
            "── 마지막 주입 ──",
            f"  토큰      : {turn.tokens}",
            f"  소요      : {turn.elapsed_ms:.1f} ms",
            f"  규칙      : {turn.observations}개",
            f"  규칙 파일 : {turn.rules_source or '없음'}",
            f"  검색 파일 : {', '.join(turn.code_files) or '없음'}",
            f"  섹션      : {', '.join(turn.sections) or '기본 프롬프트만'}",
        ]
        return "\n".join(lines)


def inject_prompt(
    base_prompt: str,
    query: str,
    *,
    store_path: str | Path | None = None,
    root: str | Path | None = None,
    config: AssemblyConfig | None = None,
    enabled: bool = True,
) -> InjectedTurn:
    """One-shot convenience wrapper. Used by the CLI and by tests."""
    store = LearnStore(store_path) if store_path else LearnStore(default_store_path())
    injector = Injector(store=store, root=root, config=config, enabled=enabled)
    return injector.prompt(base_prompt, query)


def injector_from_config(cfg: Any, root: str | Path | None = None) -> Injector:
    """Build an `Injector` from the application config, honouring its flags."""
    store_path = default_store_path()
    enabled = True
    max_tokens = 6000
    if cfg is not None:
        learn = getattr(cfg, "learn", None)
        if learn is not None:
            enabled = bool(getattr(learn, "enabled", True))
            max_tokens = int(getattr(learn, "max_context_tokens", max_tokens))
        else:
            enabled = bool(getattr(cfg, "learn_enabled", True))
    return Injector(
        store=LearnStore(store_path),
        root=root,
        config=AssemblyConfig(max_context_tokens=max_tokens),
        enabled=enabled,
    )