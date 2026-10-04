"""WUXICODE — cache binding and read-tool tests.

These cover the layer the C++ suite cannot reach: the pybind11 boundary and the
Python tools on top of it. The GIL regression test in particular is here for a
reason — the original crash built a ``py::dict`` with the GIL released, which
is invisible from C++ and only faults at concurrency.
"""

from __future__ import annotations

import asyncio
import os
import threading

import _wx_core as core
import pytest
from wx.agent.cache import canonical_args, descriptor, get_cache, reset_cache
from wx.agent.tools.read_file import ReadFileTool


@pytest.fixture
def workspace(tmp_path):
    """A real repository-shaped workspace, so the git axes have something to read."""
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    (git / "index").write_text("DIRC", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_cache()
    yield
    reset_cache()


def make_desc(cache, tool="read", args="{}", schema=1, path=None, git=True):
    d = core.CacheDescriptor()
    d.tool = tool
    d.schema_version = schema
    d.canonical_args = args
    d.canonical_path = cache.canonical(path) if path else ""
    d.use_git_axis = git
    return d


# --- git probes -----------------------------------------------------------


def test_revision_finds_the_repository(workspace):
    rev = core.git_revision(workspace)
    assert rev["valid"] is True
    assert rev["head_sha"] != 0
    assert rev["index_mtime_ns"] != 0


def test_revision_outside_a_repository_is_not_an_error(tmp_path):
    rev = core.git_revision(tmp_path)
    assert rev["valid"] is False
    assert rev["head_sha"] == 0


def test_repo_root_walks_upward(workspace):
    nested = workspace / "a" / "b" / "c"
    nested.mkdir(parents=True)
    root = core.git_repo_root(nested)
    assert os.path.normcase(root) == os.path.normcase(core.git_repo_root(workspace))


def test_stamp_reports_a_missing_path_without_raising(workspace):
    st = core.git_stamp(workspace / "nope.txt")
    assert st["exists"] is False
    assert st["size"] == 0


# --- LruCache -------------------------------------------------------------


def test_lru_roundtrip_returns_bytes():
    lru = core.LruCache(1024)
    assert lru.get(1) is None
    assert lru.put(1, b"hello") is True
    hit = lru.get(1)
    assert hit == b"hello"
    assert isinstance(hit, bytes)


def test_lru_rejects_oversized_values():
    lru = core.LruCache(64)
    assert lru.put(1, b"x" * 4096) is False
    assert lru.get(1) is None


def test_lru_hit_ratio_is_a_property():
    lru = core.LruCache(1024)
    assert 0.0 <= lru.hit_ratio <= 1.0


# --- ToolCache axes -------------------------------------------------------


def test_cache_hits_on_an_unchanged_file(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "a.txt"
    f.write_text("stable", encoding="utf-8")
    d = make_desc(cache, path=f)

    assert cache.get(d) is None
    assert cache.put(d, b"stable") is True
    assert cache.get(d) == b"stable"
    assert cache.key_for(d) == cache.key_for(d)


def test_cache_invalidates_on_size_change(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "b.txt"
    f.write_text("short", encoding="utf-8")
    d = make_desc(cache, path=f)
    cache.put(d, b"short")

    f.write_text("a much longer body", encoding="utf-8")
    assert cache.get(d) is None


def test_cache_invalidates_on_mtime_only_change(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "c.txt"
    f.write_text("same", encoding="utf-8")
    d = make_desc(cache, path=f)
    cache.put(d, b"same")

    before = cache.key_for(d)
    os.utime(f, (os.path.getatime(f), os.path.getmtime(f) + 60))
    assert cache.key_for(d) != before
    assert cache.get(d) is None


def test_cache_invalidates_on_commit(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "d.txt"
    f.write_text("content", encoding="utf-8")
    d = make_desc(cache, path=f)
    cache.put(d, b"content")

    before = cache.key_for(d)
    (workspace / ".git" / "refs" / "heads" / "main").write_text("b" * 40 + "\n", encoding="utf-8")

    assert cache.key_for(d) != before
    assert cache.get(d) is None


def test_cache_axes_are_distinct(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "e.txt"
    f.write_text("x", encoding="utf-8")

    base = make_desc(cache, path=f)
    assert cache.key_for(make_desc(cache, tool="grep", path=f)) != cache.key_for(base)
    assert cache.key_for(make_desc(cache, schema=2, path=f)) != cache.key_for(base)
    assert cache.key_for(make_desc(cache, args='{"o":1}', path=f)) != cache.key_for(base)


def test_invalidate_path_drops_entries(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "f.txt"
    f.write_text("x", encoding="utf-8")
    d = make_desc(cache, path=f)
    cache.put(d, b"x")

    assert cache.invalidate_path(cache.canonical(f)) >= 1
    assert cache.get(d) is None


def test_oversized_value_is_not_cached(workspace):
    cache = core.ToolCache(64, workspace)
    f = workspace / "g.txt"
    f.write_text("x", encoding="utf-8")
    d = make_desc(cache, path=f)

    assert cache.put(d, b"z" * 4096) is False
    # The rejected key must not linger in the path index.
    assert cache.invalidate_path(cache.canonical(f)) == 0


# --- Unicode --------------------------------------------------------------


def test_hangul_path_survives_canonicalisation(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    d = workspace / "테스트" / "한글파일.txt"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("한글", encoding="utf-8")

    canon = cache.canonical(d)
    assert "한글" in canon
    # The canonical form must reconstruct a path that still points at the file.
    assert os.path.exists(canon)

    desc = make_desc(cache, path=d)
    cache.put(desc, "한글".encode())
    assert cache.get(desc).decode("utf-8") == "한글"


# --- concurrency ----------------------------------------------------------
#
# Regression. tool_cache_stats and tool_cache_revision used to build their
# py::dict inside a py::gil_scoped_release block. Constructing a dict
# manipulates refcounts, which raced across threads and faulted at 8 threads
# while passing at 1. There is no way to catch this from C++, so it is caught
# here or not at all.


def test_stats_is_safe_under_concurrency(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "h.txt"
    f.write_text("x", encoding="utf-8")

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for i in range(300):
                d = make_desc(cache, args=f'{{"i":{i}}}', path=f)
                cache.put(d, b"x" * 64)
                cache.get(d)
                cache.stats()
                cache.revision()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent cache use raised: {errors[:3]}"


def test_invalidate_path_is_safe_under_concurrency(workspace):
    cache = core.ToolCache(1 << 20, workspace)
    f = workspace / "i.txt"
    f.write_text("x", encoding="utf-8")

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for i in range(100):
                cache.put(make_desc(cache, args=f'{{"i":{i}}}', path=f), b"x")
                cache.invalidate_path(cache.canonical(f))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent invalidation raised: {errors[:3]}"


# --- read tool ------------------------------------------------------------


def test_read_tool_serves_unchanged_file_from_cache(workspace):
    f = workspace / "read.txt"
    f.write_text("hello", encoding="utf-8")
    tool = ReadFileTool(str(workspace))

    assert asyncio.run(tool.execute(path="read.txt")) == "hello"
    stats_after_first = get_cache(str(workspace)).stats()["lru"]
    assert stats_after_first["inserts"] >= 1

    assert asyncio.run(tool.execute(path="read.txt")) == "hello"
    stats = get_cache(str(workspace)).stats()["lru"]
    assert stats["hits"] > stats_after_first["hits"]


def test_read_tool_never_serves_stale_content(workspace):
    f = workspace / "stale.txt"
    f.write_text("version one", encoding="utf-8")
    tool = ReadFileTool(str(workspace))

    assert asyncio.run(tool.execute(path="stale.txt")) == "version one"

    f.write_text("version two is longer", encoding="utf-8")
    assert asyncio.run(tool.execute(path="stale.txt")) == "version two is longer"


def test_read_tool_reports_missing_file_and_does_not_cache_the_error(workspace):
    tool = ReadFileTool(str(workspace))
    target = workspace / "later.txt"

    assert "찾을 수 없습니다" in asyncio.run(tool.execute(path="later.txt"))

    target.write_text("now here", encoding="utf-8")
    # A cached "not found" would still be reported here.
    assert asyncio.run(tool.execute(path="later.txt")) == "now here"


def test_read_tool_handles_empty_file(workspace):
    (workspace / "empty.txt").write_text("", encoding="utf-8")
    tool = ReadFileTool(str(workspace))
    assert asyncio.run(tool.execute(path="empty.txt")) == "(빈 파일)"


def test_read_tool_detects_binary(workspace):
    (workspace / "bin.dat").write_bytes(b"\xff\xfe\x00\x01\x02binary")
    tool = ReadFileTool(str(workspace))
    out = asyncio.run(tool.execute(path="bin.dat"))
    assert "이진 파일" in out


def test_read_tool_handles_hangul(workspace):
    (workspace / "한글.txt").write_text("안녕하세요", encoding="utf-8")
    tool = ReadFileTool(str(workspace))
    assert asyncio.run(tool.execute(path="한글.txt")) == "안녕하세요"
    # Second read must come back identical from cache, not through the decoder.
    assert asyncio.run(tool.execute(path="한글.txt")) == "안녕하세요"


def test_read_tool_rejects_paths_outside_the_workspace(workspace):
    tool = ReadFileTool(str(workspace))
    out = asyncio.run(tool.execute(path="../outside.txt"))
    assert "escapes workspace" in out


# --- canonical_args -------------------------------------------------------


def test_canonical_args_is_order_independent():
    assert canonical_args({"a": 1, "b": 2}) == canonical_args({"b": 2, "a": 1})


def test_canonical_args_does_not_escape_hangul():
    # Escaped form would still be deterministic, but it would differ from the
    # bytes the C++ side sees for the same logical string.
    assert "한" in canonical_args({"path": "한글.txt"})


def test_descriptor_excludes_path_from_the_argument_axis(workspace):
    """Two spellings of one path must land on ONE cache entry.

    `descriptor()` keeps `path` out of the argument blob precisely so that
    `./a.txt`, `a.txt` and the absolute form do not become three entries for
    one file. If path were also in axis 3, this key comparison would fail.
    """
    cache = get_cache(str(workspace))
    f = workspace / "j.txt"
    f.write_text("x", encoding="utf-8")

    abs_form = descriptor("read", str(workspace), path=str(f), args={})
    rel_form = descriptor("read", str(workspace), path="./j.txt", args={})

    assert abs_form.canonical_args == "{}"
    assert rel_form.canonical_args == "{}"
    assert abs_form.canonical_path == rel_form.canonical_path
    assert cache.key_for(abs_form) == cache.key_for(rel_form)
