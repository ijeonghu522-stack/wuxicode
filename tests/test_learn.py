"""Tests for the learning layer and the tool loop it feeds.

Run with:  python -m pytest tests/test_learn.py -q

The store is created under `tmp_path` for every test. Nothing here touches the
user's real store, and nothing here reaches the network: the loop is tested
against a stub client that replays a scripted event sequence, because a test
that needs an API key is a test that does not run.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from wx.agent.events import (
    ToolCallFinished,
    ToolCallStarted,
    TurnDone,
)
from wx.agent.loop import AgentLoop
from wx.context.assembly import (
    AssemblyConfig,
    build_system_prompt,
    estimate_tokens,
    load_project_rules,
)
from wx.learn.capture import Capture
from wx.learn.digest import (
    BEGIN_MARKER,
    END_MARKER,
    publish,
    run_digest,
)
from wx.learn.extract import extract, normalise_error
from wx.learn.index import index_repository, reindex_now, search_code
from wx.learn.inject import Injector
from wx.learn.store import LearnStore


@pytest.fixture()
def store(tmp_path: Path) -> LearnStore:
    s = LearnStore(tmp_path / "learn.db")
    s.initialize()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_schema_and_roundtrip(store: LearnStore) -> None:
    assert store.healthy()
    turn_id = store.record_turn("s1", "user", "merge_adapter 들여쓰리 고쳐줘")
    assert turn_id > 0
    turns = store.recent_turns(limit=5)
    assert turns[-1]["role"] == "user"
    assert "들여쓰리" in turns[-1]["content"]


def test_store_observation_dedupes(store: LearnStore) -> None:
    first = store.add_observation("convention", "들여쓰기는 2 스페이스")
    second = store.add_observation("convention", "들여쓰기는 2 스페이스")
    assert first == second > 0
    assert len(store.observations(kind="convention")) == 1


def test_store_korean_fulltext_search(store: LearnStore) -> None:
    store.add_observation("convention", "YAML 들여쓰기는 두 스페이스를 유지한다")
    store.add_observation("fact", "Kaggle 시스템 RAM 은 29GB")
    hits = store.observations(query="스페이스")
    assert any("YAML" in o.text for o in hits)


def test_store_recency_and_hits_rank(store: LearnStore) -> None:
    old = store.add_observation("fact", "오래된 규칙")
    store.add_observation("fact", "새로운 규칙")
    fresh = store.observations(limit=10)[0]
    assert fresh.text == "새로운 규칙"
    store.mark_used([old])
    again = store.observations(limit=10)
    assert again[0].text == "오래된 규칙"


def test_store_prune_drops_stale(store: LearnStore) -> None:
    store.add_observation("fact", "오래된 것")
    store.conn.execute("UPDATE observations SET ts = ?", (time.time() - 400 * 86400,))
    store.conn.commit()
    assert store.prune(keep_days=180.0) == 1
    assert store.observations() == []


def test_store_survives_unwritable_path(tmp_path: Path) -> None:
    # A directory where the database file should be: every write fails, and
    # none of the public methods may raise.
    bad = LearnStore(tmp_path / "subdir")
    (tmp_path / "subdir").mkdir()
    assert bad.record_tool_call(name="bash", args="x", result="y", ok=True) == 0
    assert bad.add_observation("fact", "x") == 0
    assert bad.observations() == []
    assert bad.recent_turns() == []
    assert bad.stats()["observations"] == 0


# ---------------------------------------------------------------------------
# indexing
# ---------------------------------------------------------------------------


def _make_tree(root: Path) -> None:
    (root / "python" / "wx").mkdir(parents=True)
    (root / "training").mkdir()
    (root / "python" / "wx" / "merge.py").write_text(
        "def merge(base, adapter):\n    # LoRA 어댑터를 병합한다\n    return base\n",
        encoding="utf-8",
    )
    (root / "python" / "wx" / "loop.py").write_text(
        "async def run(user_input):\n    # 에이전트 루프\n    yield\n",
        encoding="utf-8",
    )
    (root / "training" / "config.yaml").write_text(
        "base_model: Qwen/Qwen2.5-Coder-7B-Instruct\nmax_samples: 5000\n",
        encoding="utf-8",
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("const 여기는 = 1;", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "merge.cpython-312.pyc").write_bytes(b"\x00\x01binary")


def test_index_skips_noise_and_finds_korean(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    summary = index_repository(store, tmp_path)
    assert summary["indexed"] >= 3
    assert summary["errors"] == 0

    hits = search_code(store, "어댑터 병합")
    assert hits, "한국어 질의로 검색되어야 한다"
    assert any("merge.py" in h.path for h in hits)

    paths = {r["path"] for r in store.conn.execute("SELECT path FROM code_files")}
    assert not any("node_modules" in p for p in paths)
    assert not any("__pycache__" in p for p in paths)


def test_index_incremental_skips_unchanged(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    first = index_repository(store, tmp_path)
    assert first["indexed"] > 0
    second = index_repository(store, tmp_path)
    assert second["indexed"] == 0
    assert second["unchanged"] == first["indexed"]


def test_index_prunes_deleted_files(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    index_repository(store, tmp_path)
    (tmp_path / "training" / "config.yaml").unlink()
    summary = index_repository(store, tmp_path)
    assert summary["removed"] == 1
    assert not search_code(store, "Qwen2.5-Coder")


def test_reindex_forces_full_read(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    index_repository(store, tmp_path)
    summary = reindex_now(store, tmp_path)
    assert summary["indexed"] >= 3


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_normalise_error_collapses_varying_tokens() -> None:
    a = normalise_error("ERROR: build failed at line 42 in /home/u/proj/src/a.py after 3.5s")
    b = normalise_error("ERROR: build failed at line 91 in /home/v/other/src/b.py after 7.1s")
    assert a == b
    assert "<n>" in a and "<path>" in a


def test_normalise_error_keeps_distinct_kinds_apart() -> None:
    a = normalise_error("ModuleNotFoundError: No module named 'torch'")
    b = normalise_error("SyntaxError: invalid syntax at line 3")
    assert a != b


def test_extract_builds_antipattern_and_recipe(store: LearnStore) -> None:
    for _ in range(3):
        store.record_tool_call(name="bash", args='{"command":"npm run build"}',
                               result="error", ok=False)
    for _ in range(4):
        store.record_tool_call(name="bash", args='{"command":"pytest -q"}',
                               result="12 passed", ok=True)

    result = extract(store, window_days=7.0)
    assert result.added >= 2
    kinds = {o.kind for o in store.observations()}
    assert "antipattern" in kinds
    assert "recipe" in kinds


def test_extract_ignores_single_failures(store: LearnStore) -> None:
    store.record_tool_call(name="bash", args='{"command":"ls"}', result="err", ok=False)
    result = extract(store, window_days=7.0)
    assert result.added == 0


def test_extract_detects_hotspot(store: LearnStore) -> None:
    for _ in range(6):
        store.record_tool_call(
            name="edit_file", args=json.dumps({"path": "src/core.py"}),
            result="ok", ok=True,
        )
    extract(store, window_days=7.0)
    hot = store.observations(kind="hotspot")
    assert hot and "src/core.py" in hot[0].text


def test_extract_is_idempotent(store: LearnStore) -> None:
    for _ in range(3):
        store.record_tool_call(name="bash", args='{"command":"npm run build"}',
                               result="error", ok=False)
    first = extract(store, window_days=7.0)
    before = len(store.observations())
    second = extract(store, window_days=7.0)
    assert first.added == second.added
    assert len(store.observations()) == before


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_reassembles_interleaved_tool_deltas(store: LearnStore) -> None:
    cap = Capture(store, session_id="sess1")
    record = cap.begin_turn()
    # Two calls whose fragments arrive interleaved, the documented hazard.
    record.add_tool_delta(0, "read_file", '{"pa', "call_a")
    record.add_tool_delta(1, "bash", '{"comm', "call_b")
    record.add_tool_delta(0, None, 'th": "a.py"}', None)
    record.add_tool_delta(1, None, 'and": "ls"}', None)

    calls = record.resolved_calls()
    assert [c["name"] for c in calls] == ["read_file", "bash"]
    assert calls[0]["arguments"] == {"path": "a.py"}
    assert calls[1]["arguments"] == {"command": "ls"}


def test_capture_keeps_malformed_arguments_visible(store: LearnStore) -> None:
    record = Capture(store).begin_turn()
    record.add_tool_delta(0, "bash", "{not json", "c1")
    args = record.resolved_calls()[0]["arguments"]
    assert args["__raw__"] == "{not json"


def test_capture_records_outcomes(store: LearnStore) -> None:
    cap = Capture(store, session_id="s2")
    cap.begin_turn()
    cap.set_user_text("테스트 돌려")
    cap.record_tool_result(name="bash", arguments={"command": "pytest"},
                           result="12 passed")
    cap.record_tool_error(name="bash", arguments={"command": "ruff"},
                          error="SyntaxError")
    assert store.repeated_failures() == []
    assert len(store.tool_calls_since(0)) == 2
    assert store.stats()["tool_calls"] == 2


def test_capture_never_raises_on_dead_store(tmp_path: Path) -> None:
    # A directory where the database file should be. The path has to be inside
    # tmp_path: a literal "/nonexistent-root/..." resolves against the current
    # drive on Windows, where the process may well be able to create it, and the
    # store then opens fine and the test asserts nothing.
    bad = LearnStore(tmp_path / "subdir")
    (tmp_path / "subdir").mkdir()
    assert not bad.healthy()
    cap = Capture(bad)
    cap.begin_turn()
    cap.set_user_text("hi")
    assert cap.record_tool_result(name="bash", arguments={}, result="r") == 0
    assert cap.note("fact", "x") == 0
    assert cap.close()["turns"] == 1


# ---------------------------------------------------------------------------
# assembly + injection
# ---------------------------------------------------------------------------


def test_assembly_with_empty_store_returns_base(store: LearnStore, tmp_path: Path) -> None:
    a = build_system_prompt("You are WX.", store=store, query="아무것도", root=tmp_path)
    assert a.system_prompt == "You are WX."
    assert a.tokens > 0


def test_assembly_injects_learned_rules(store: LearnStore, tmp_path: Path) -> None:
    store.add_observation("antipattern", "torch_dtype= 은 transformers 4.56 에서 deprecated")
    a = build_system_prompt("You are WX.", store=store, query="병합 dtype", root=tmp_path)
    assert "## 이 저장소에서 배운 것" in a.system_prompt
    assert "deprecated" in a.system_prompt
    assert a.observations_used


def test_assembly_injects_retrieved_files(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    index_repository(store, tmp_path)
    a = build_system_prompt(
        "You are WX.", store=store, query="어댑터 병합 함수", root=tmp_path
    )
    assert "## 관련 파일" in a.system_prompt
    assert any("merge.py" in p for p in a.code_paths)


def test_assembly_respects_token_ceiling(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    index_repository(store, tmp_path)
    for i in range(60):
        store.add_observation("fact", f"규칙 {i} " + ("가나다라마바" * 40))
    cfg = AssemblyConfig(max_context_tokens=800, max_system_tokens=800, max_observations=40)
    a = build_system_prompt("You are WX.", store=store, query="병합", root=tmp_path, config=cfg)
    assert a.tokens <= 900, f"예산을 초과했다: {a.tokens}"


def test_assembly_loads_project_rule_files(tmp_path: Path) -> None:
    (tmp_path / "WX.md").write_text("- 들여쓰기 2 스페이스\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("- 한국어 주석\n", encoding="utf-8")
    text, source = load_project_rules(tmp_path)
    assert "WX.md" in source and "AGENTS.md" in source
    assert "2 스페이스" in text and "한국어 주석" in text


def test_estimate_tokens_handles_korean_and_ascii() -> None:
    korean = "이 저장소의 들여쓰기는 두 스페이스를 유지해야 합니다" * 20
    ascii_ = "def merge(base, adapter):\n    return base\n" * 20
    assert estimate_tokens(korean) > 0
    # Korean is denser per character than ASCII; both must be non-zero and
    # the Korean count must be larger for equal length.
    assert estimate_tokens(korean[:len(ascii_)]) > 0


def test_injector_disabled_returns_base(store: LearnStore, tmp_path: Path) -> None:
    store.add_observation("fact", "무시될 규칙")
    inj = Injector(store=store, root=tmp_path, enabled=False)
    turn = inj.prompt("You are WX.", "질문")
    assert turn.prompt == "You are WX."
    assert turn.observations == 0


def test_injector_marks_used_observations(store: LearnStore, tmp_path: Path) -> None:
    obs_id = store.add_observation("fact", "반복해서 쓰이는 규칙")
    assert store.observations()[0].hits == 0
    Injector(store=store, root=tmp_path).prompt("You are WX.", "규칙")
    assert store.observations()[0].hits == 1
    assert obs_id > 0


def test_injector_survives_broken_store(tmp_path: Path) -> None:
    (tmp_path / "blocked").mkdir()
    inj = Injector(store=LearnStore(tmp_path / "blocked"), root=tmp_path)
    assert inj.prompt("You are WX.", "질문").prompt == "You are WX."


def test_injection_is_fast(store: LearnStore, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    index_repository(store, tmp_path)
    for i in range(200):
        store.add_observation("fact", f"규칙 {i}: merge_adapter 들여쓰기 두 스페이스")
    inj = Injector(store=store, root=tmp_path)
    inj.prompt("You are WX.", "병합 인자 들여쓰기")  # warm the page cache
    turn = inj.prompt("You are WX.", "병합 인자 들여쓰기")
    assert turn.elapsed_ms < 500.0, f"주입이 {turn.elapsed_ms:.1f}ms — 500ms 초과"


# ---------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------


def test_digest_publishes_and_preserves_hand_edits(store: LearnStore, tmp_path: Path) -> None:
    for _ in range(3):
        store.record_tool_call(name="bash", args='{"command":"npm run build"}',
                               result="error", ok=False)
    out = tmp_path / "learned-rules.md"
    extract(store)

    publish(store, path=out)
    first = out.read_text(encoding="utf-8")
    assert BEGIN_MARKER in first and END_MARKER in first
    assert "하지 말 것" in first

    # A human adds a rule outside the markers.
    edited = first + "\n## 내가 직접 쓴 규칙\n\n- 이 저장소에서는 ruff 를 먼저 돌린다.\n"
    out.write_text(edited, encoding="utf-8")

    publish(store, path=out)
    second = out.read_text(encoding="utf-8")
    assert "ruff 를 먼저 돌린다" in second, "마커 밖 수동 편집은 보존되어야 한다"
    assert "하지 말 것" in second


def test_publish_refuses_to_clobber_unmarked_file(store: LearnStore, tmp_path: Path) -> None:
    out = tmp_path / "learned-rules.md"
    out.write_text("중요한 수동 파일, 마커 없음", encoding="utf-8")
    with pytest.raises(ValueError):
        publish(store, path=out)
    assert "중요한 수동 파일" in out.read_text(encoding="utf-8")
    assert out.with_suffix(".md.new").exists()


def test_run_digest_end_to_end(store: LearnStore, tmp_path: Path) -> None:
    for _ in range(3):
        store.record_tool_call(name="bash", args='{"command":"npm run build"}',
                               result="error", ok=False)
    report = run_digest(store, path=tmp_path / "learned-rules.md", window_days=7.0)
    assert report.added >= 1
    assert report.written_to
    assert "WX 학습 다이제스트" in report.report_text
    assert report.notes == []


# ---------------------------------------------------------------------------
# tool loop
# ---------------------------------------------------------------------------


class _StubClient:
    """Replays a scripted list of event batches, one per model call."""

    def __init__(self, batches: list[list]) -> None:
        self.batches = batches
        self.calls: list[dict] = []

    def stream(self, messages, **params):
        self.calls.append({"messages": list(messages), "params": dict(params)})
        batch = self.batches.pop(0) if self.batches else []
        return self._gen(batch)

    async def _gen(self, batch):
        for ev in batch:
            yield ev


class _EchoTool:
    name = "echo"
    description = "echo the message"

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}},
            },
        }

    async def execute(self, **kwargs):
        return f"echo: {kwargs.get('msg', '')}"


class _BoomTool:
    name = "boom"
    description = "always fails"

    def schema(self):
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description,
                         "parameters": {"type": "object", "properties": {}}},
        }

    async def execute(self, **kwargs):
        raise RuntimeError("의도한 실패")


def _tool_delta(index, name, args, call_id):
    from wx.llm.openai_client import ToolCallDelta

    return ToolCallDelta(index=index, name=name, arguments=args, call_id=call_id)


def _run_loop(loop_obj, text):
    async def go():
        return [ev async for ev in loop_obj.run(text)]

    return asyncio.run(go())


def test_loop_single_pass_without_tools() -> None:
    from wx.llm.openai_client import TextChunk

    client = _StubClient([[TextChunk(text="안녕")]])
    events = _run_loop(AgentLoop(client), "hi")
    assert any(e.text == "안녕" for e in events if type(e).__name__ == "TextDelta")
    assert isinstance(events[-1], TurnDone)
    assert events[-1].tool_calls == 0
    assert events[-1].iterations == 1


def test_loop_executes_tool_then_continues() -> None:
    from wx.llm.openai_client import TextChunk

    client = _StubClient([
        [_tool_delta(0, "echo", '{"msg":"ping"}', "c1")],
        [TextChunk(text="완료")],
    ])
    loop_obj = AgentLoop(client, tools=[_EchoTool()])
    events = _run_loop(loop_obj, "ping 해줘")

    started = [e for e in events if isinstance(e, ToolCallStarted)]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert len(started) == 1 and started[0].name == "echo"
    assert len(finished) == 1 and finished[0].ok is True

    done = events[-1]
    assert isinstance(done, TurnDone)
    assert done.tool_calls == 1
    assert done.iterations == 2

    # The second request must carry the assistant tool_call and the tool result.
    second = client.calls[1]["messages"]
    roles = [m.get("role") for m in second]
    assert "tool" in roles
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert tool_msg["content"] == "echo: ping"
    assert "tools" in client.calls[0]["params"]


def test_loop_tool_failure_becomes_observation_not_crash() -> None:
    from wx.llm.openai_client import TextChunk

    client = _StubClient([
        [_tool_delta(0, "boom", "{}", "c1")],
        [TextChunk(text="실패했네")],
    ])
    events = _run_loop(AgentLoop(client, tools=[_BoomTool()]), "실행해")
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert finished and finished[0].ok is False
    assert "의도한 실패" in (finished[0].error or "")
    assert isinstance(events[-1], TurnDone)


def test_loop_unknown_tool_is_reported_to_model() -> None:
    from wx.llm.openai_client import TextChunk

    client = _StubClient([
        [_tool_delta(0, "nonexistent", "{}", "c1")],
        [TextChunk(text="알겠어")],
    ])
    events = _run_loop(AgentLoop(client, tools=[_EchoTool()]), "해봐")
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert finished and finished[0].ok is False
    assert "echo" in (finished[0].error or "")


def test_loop_respects_iteration_cap() -> None:
    # Every batch asks for a tool, so only the cap can stop it.
    client = _StubClient([[_tool_delta(0, "echo", '{"msg":"x"}', "c1")] for _ in range(20)])
    loop_obj = AgentLoop(client, tools=[_EchoTool()], max_iterations=4)
    events = _run_loop(loop_obj, "반복")
    done = events[-1]
    assert isinstance(done, TurnDone)
    assert done.iterations == 4
    assert done.tool_calls == 4


def test_loop_injects_learned_prompt_and_captures(store: LearnStore, tmp_path: Path) -> None:
    from wx.llm.openai_client import TextChunk

    store.add_observation("convention", "이 저장소는 한국어 주석을 쓴다")
    client = _StubClient([
        [_tool_delta(0, "echo", '{"msg":"hi"}', "c1")],
        [TextChunk(text="끝")],
    ])
    cap = Capture(store, session_id="sess-loop")
    inj = Injector(store=store, root=tmp_path)
    loop_obj = AgentLoop(
        client, tools=[_EchoTool()], injector=inj, capture=cap, session_id="sess-loop"
    )
    events = _run_loop(loop_obj, "뭘 해야 하지?")

    system = client.calls[0]["messages"][0]["content"]
    assert "이 저장소에서 배운 것" in system
    assert "한국어 주석" in system

    assert isinstance(events[-1], TurnDone)
    assert store.stats()["tool_calls"] >= 1
    assert store.stats()["turns"] >= 1


def test_loop_runs_unchanged_when_learning_disabled(store: LearnStore, tmp_path: Path) -> None:
    from wx.llm.openai_client import TextChunk

    store.add_observation("convention", "주입되면 안 되는 규칙")
    client = _StubClient([[TextChunk(text="ok")]])
    inj = Injector(store=store, root=tmp_path, enabled=False)
    loop_obj = AgentLoop(client, injector=inj)
    _run_loop(loop_obj, "질문")
    system = client.calls[0]["messages"][0]["content"]
    assert "주입되면 안 되는 규칙" not in system
    assert system == loop_obj.system_prompt


def test_loop_clips_large_tool_output() -> None:
    from wx.agent.loop import _clip
    from wx.llm.openai_client import TextChunk

    class _BigTool(_EchoTool):
        async def execute(self, **kwargs):
            return "HEAD_MARK\n" + ("x" * 50_000) + "\nTAIL_MARK"

    client = _StubClient([
        [_tool_delta(0, "echo", "{}", "c1")],
        [TextChunk(text="done")],
    ])
    _run_loop(AgentLoop(client, tools=[_BigTool()]), "go")
    tool_msg = next(m for m in client.calls[1]["messages"] if m["role"] == "tool")
    content = tool_msg["content"]
    assert len(content) < 8000
    assert "HEAD_MARK" in content and "TAIL_MARK" in content
    assert _clip("짧다") == "짧다"