"""WUXICODE — shared tool result cache.

One process-wide ``ToolCache`` shared by every tool. Two caches would mean two
byte budgets, and the aggregate resident set would no longer be bounded by
anything the user chose.

WHAT IS CACHED: raw file bytes, never a formatted result string. Formatting is
cheap next to the read it replaces, and keeping it out of the cache means a
change to a message ("(빈 파일)" wording) does not silently invalidate every
entry ever stored.

WHAT IS NOT CACHED: errors. A file that does not exist yet is a normal state
that changes; serving a cached "not found" would make the agent believe a file
it just created is missing.

The native module is imported through ``_import_core``, which degrades to an
uncached cache if the extension is missing. That is a broken-install situation,
not a design choice, so it warns once and loudly rather than failing every read
with an opaque ImportError.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import typing

if typing.TYPE_CHECKING:
    from typing import Any

# 160 MB. Sized for a long agent session on the low-spec machines this project
# targets: enough that ordinary reads never evict, small enough to be invisible
# next to the Python interpreter and the model client.
DEFAULT_CAPACITY_BYTES = 160 * 1024 * 1024

_warned = False

try:
    import _wx_core
except ImportError:  # pragma: no cover - only on a source tree that was never built
    _wx_core = None  # type: ignore[assignment]


def _require_core() -> Any:
    global _warned
    if _wx_core is None and not _warned:
        _warned = True
        print(
            "[wx] WARNING: _wx_core extension is not built. Tool results will "
            "not be cached. Run `pip install -e .` to build it.",
            file=sys.stderr,
        )
    return _wx_core


def canonical_args(args: dict[str, Any]) -> str:
    """Serialise arguments deterministically for cache axis 3.

    ``sort_keys`` so argument order cannot change the key, and ``ensure_ascii
    =False`` so a Korean path hashes to the same bytes it does in C++ rather
    than to an escaped form.
    """
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def descriptor(
    tool: str,
    workspace_root: str,
    *,
    path: str | None = None,
    args: dict[str, Any] | None = None,
    schema_version: int = 1,
    use_git_axis: bool = True,
) -> Any:
    """Build a CacheDescriptor for one tool call.

    ``path`` is passed separately from ``args`` because it is axis 5, not axis 3.
    Including it in the argument blob as well would split one file's entries in
    two the moment somebody wrote ``./a/b`` instead of ``a/b`` — which is
    precisely the redundancy the canonical form exists to remove.

    A relative ``path`` is resolved against ``workspace_root``, not against the
    process working directory. Those are frequently different — the CWD is
    wherever the developer launched the CLI, and the workspace is wherever the
    agent is pointed — and resolving against the CWD would silently key entries
    to a file the caller never named. This mirrors ``Tool.validate_path``.
    """
    core = _require_core()
    cache = get_cache(workspace_root)
    d = core.CacheDescriptor()
    d.tool = tool
    d.schema_version = schema_version
    d.canonical_args = canonical_args(args or {})
    if path:
        abs_path = path if os.path.isabs(path) else os.path.join(workspace_root, path)
        d.canonical_path = cache.canonical(os.path.abspath(abs_path))
    else:
        d.canonical_path = ""
    d.use_git_axis = use_git_axis
    return d


_lock = threading.Lock()
_cache: Any = None
_cache_root: str | None = None


def get_cache(workspace_root: str | None = None) -> Any:
    """Return the process-wide cache, creating it on first use.

    Rebuilt if the workspace root changes. Tools are constructed per session
    with the session's root, and holding a cache keyed to the wrong repository
    would invalidate on every lookup.
    """
    core = _require_core()
    root = os.path.abspath(workspace_root or os.getcwd())
    global _cache, _cache_root
    with _lock:
        if _cache is None or _cache_root != root:
            _cache = core.ToolCache(DEFAULT_CAPACITY_BYTES, root)
            _cache_root = root
        return _cache


def reset_cache() -> None:
    """Drop the process-wide cache. Tests only."""
    global _cache, _cache_root
    with _lock:
        _cache = None
        _cache_root = None


def cache_available() -> bool:
    return _wx_core is not None


def cache_stats(workspace_root: str | None = None) -> dict[str, Any] | None:
    """Current cache counters, or None when the extension is unavailable."""
    if not cache_available():
        return None
    return get_cache(workspace_root).stats()
