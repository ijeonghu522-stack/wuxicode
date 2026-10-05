"""WUXICODE — the learning layer.

Four capabilities, one storage substrate:

    capture   record what happened. Turns, tool calls, their outcomes.
    extract   distil rules from the record. Statistical, no model, nightly.
    inject    put those rules into the next turn's system prompt.
    digest    the nightly job that ties extract to a readable report.

Plus `index`, which makes the repository itself searchable so a turn can
retrieve the files a question is about instead of guessing at paths.

The layer is optional at every entry point. Nothing in `wx` outside this
package imports it, so a machine without a store, without FTS5, or with
learning switched off behaves exactly as it did before this package existed.

    >>> from wx.learn import LearnStore, Injector, run_digest
    >>> store = LearnStore()
    >>> report = run_digest(store)
    >>> print(report.report_text)
"""

from __future__ import annotations

__all__ = [
    "LearnStore",
    "Capture",
    "Extractor",
    "Injector",
    "run_digest",
    "index_repository",
    "search_code",
]


def __getattr__(name: str):
    # Deferred so that importing `wx.learn` does not drag the whole package —
    # and therefore sqlite3 — into a process that only wants one symbol.
    if name in ("LearnStore", "Observation", "ToolRecord", "default_store_path", "open_store"):
        from wx.learn import store as _store

        return getattr(_store, name)
    if name == "Capture":
        from wx.learn.capture import Capture

        return Capture
    if name == "Extractor":
        from wx.learn.extract import extract as _extract

        return _extract
    if name == "Injector":
        from wx.learn.inject import Injector

        return Injector
    if name == "run_digest":
        from wx.learn.digest import run_digest as _run

        return _run
    if name in ("index_repository", "search_code"):
        from wx.learn import index as _index

        return getattr(_index, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")