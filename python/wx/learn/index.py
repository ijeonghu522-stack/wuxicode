"""WUXICODE — repository indexer.

Walks a workspace and puts its text into the store's FTS table so a turn can
retrieve the handful of lines that matter instead of the whole tree.

WHAT IS INDEXED: source and documentation files, each stored as one row.
File-per-row rather than chunk-per-row on purpose. The retrieval unit that
matters for a coding agent is "this file", because the follow-up action is
almost always "read this file" — handing the model a mid-file fragment with
no surrounding context produces edits that ignore the imports above it.

WHAT IS NOT: anything in `_SKIP_DIRS`, anything past `MAX_INDEX_FILE_BYTES`,
anything that fails a UTF-8 decode. A binary file that decodes by accident
produces garbage rows that then match queries nobody asked.

INCREMENTAL: mtime and size are compared against the stored row. Re-indexing a
tree that has not changed costs one stat per file and no writes. This runs on
a background thread at session start, so it must be cheap when it has nothing
to do.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

from wx.learn.store import (
    MAX_INDEX_FILE_BYTES,
    _LANGS,
    _SKIP_DIRS,
    LearnStore,
    _fts_query,
)

__all__ = ["index_repository", "index_stats", "CodeHit", "search_code"]

__all__ += ["_SKIP_DIRS", "_LANGS"]  # re-exported for tests that want the tables


class CodeHit:
    """One retrieved file, with the reason it was retrieved.

    `score` is FTS bm25 (lower is better, already negated so higher is
    better) combined with a small boost for shallow paths — the entry point
    is usually a better context seed than something six directories deep.
    """

    __slots__ = ("path", "lang", "body", "score")

    def __init__(self, path: str, lang: str, body: str, score: float) -> None:
        self.path = path
        self.lang = lang
        self.body = body
        self.score = score

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "lang": self.lang, "score": self.score}

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"CodeHit({self.score:.3f}, {self.path!r})"


def _iter_source_files(root: Path, max_files: int) -> Iterator[Path]:
    """Depth-first walk yielding indexable files.

    Prunes whole directories at the walk level rather than filtering afterwards,
    because the expensive part is the syscall, not the comparison. `os.walk`
    lets us delete entries from `dirnames` in place to stop descending.
    """
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk never descends into them. os.walk never
        # yields "." in dirnames, so a plain dot-prefix test is safe here.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            # Unknown suffixes are kept: a project-specific format the user
            # works in daily is exactly the kind of file retrieval should find.
            path = Path(dirpath) / name
            yield path
            seen += 1
            if seen >= max_files:
                return


def index_repository(
    store: LearnStore,
    root: str | Path,
    *,
    max_files: int = 4000,
    prune_missing: bool = True,
) -> dict[str, int]:
    """Index `root` into the store. Returns a count summary.

    Never raises. A failure here degrades retrieval, and degraded retrieval is
    strictly better than a session that refuses to start.
    """
    root_path = Path(root).resolve()
    summary = {"indexed": 0, "skipped": 0, "removed": 0, "unchanged": 0, "errors": 0}
    if not root_path.is_dir():
        return summary

    seen_paths: set[str] = set()

    try:
        store.initialize()
    except Exception:
        summary["errors"] += 1
        return summary

    try:
        known = {
            row["path"]: (row["mtime"], row["size"])
            for row in store.conn.execute("SELECT path, mtime, size FROM code_files").fetchall()
        }
    except Exception:
        known = {}

    for path in _iter_source_files(root_path, max_files):
        rel = path.relative_to(root_path).as_posix()
        seen_paths.add(rel)
        try:
            st = path.stat()
            if st.st_size > MAX_INDEX_FILE_BYTES:
                summary["skipped"] += 1
                continue
            prior = known.get(rel)
            if prior and prior[0] == st.st_mtime and prior[1] == st.st_size:
                summary["unchanged"] += 1
                continue
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            summary["skipped"] += 1
            continue
        except Exception:
            summary["errors"] += 1
            continue

        lang = _LANGS.get(path.suffix.lower(), path.suffix.lstrip(".").lower())
        try:
            store.conn.execute("DELETE FROM code_fts WHERE path = ?", (rel,))
            store.conn.execute(
                "INSERT INTO code_fts(path, lang, body) VALUES(?,?,?)", (rel, lang, body)
            )
            store.conn.execute(
                "INSERT OR REPLACE INTO code_files(path, mtime, size, lang) VALUES(?,?,?,?)",
                (rel, st.st_mtime, st.st_size, lang),
            )
            summary["indexed"] += 1
        except Exception:
            summary["errors"] += 1

    if prune_missing:
        gone = [p for p in known if p not in seen_paths]
        for rel in gone:
            try:
                store.conn.execute("DELETE FROM code_fts WHERE path = ?", (rel,))
                store.conn.execute("DELETE FROM code_files WHERE path = ?", (rel,))
                summary["removed"] += 1
            except Exception:
                summary["errors"] += 1

    try:
        store.conn.commit()
    except Exception:
        summary["errors"] += 1
    return summary


def search_code(store: LearnStore, query: str, limit: int = 5) -> list[CodeHit]:
    """Retrieve the most relevant indexed files for `query`.

    Returns whole file bodies. Callers are responsible for trimming to a token
    budget — `context.assembly` does that, and doing it here would mean two
    places that both think they own the budget.
    """
    expression = _fts_query(query)
    if not expression:
        return []
    try:
        store.initialize()
        rows = store.conn.execute(
            "SELECT path, lang, body, bm25(code_fts, 1.0, 1.0, 0.5) AS rank "
            "FROM code_fts WHERE code_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (expression, limit * 3),
        ).fetchall()
    except Exception:
        return []

    hits: list[CodeHit] = []
    for row in rows:
        depth = row["path"].count("/")
        # bm25 is negative-is-better; negate and add a shallow-path bonus.
        score = -row["rank"] + 0.05 / (1.0 + depth)
        hits.append(CodeHit(row["path"], row["lang"], row["body"], score))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def index_stats(store: LearnStore) -> dict[str, object]:
    """Counts and language histogram for the indexed corpus."""
    try:
        store.initialize()
        total = store.conn.execute("SELECT COUNT(*) n FROM code_files").fetchone()["n"]
        by_lang = store.conn.execute(
            "SELECT lang, COUNT(*) n FROM code_files GROUP BY lang ORDER BY n DESC LIMIT 12"
        ).fetchall()
        bytes_total = store.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(body)),0) n FROM code_fts"
        ).fetchone()["n"]
        last = store.conn.execute("SELECT MAX(mtime) m FROM code_files").fetchone()["m"]
        return {
            "files": total,
            "bytes": bytes_total,
            "languages": {r["lang"] or "?": r["n"] for r in by_lang},
            "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last))
            if last
            else "never",
        }
    except Exception:
        return {"files": 0, "error": "unavailable"}


def reindex_now(store: LearnStore, root: str | Path, **kwargs) -> dict[str, int]:
    """Force a full re-read, ignoring the mtime cache.

    The incremental path trusts mtime and size. That is true almost always and
    wrong on the cases that matter: a checkout that rewrites files preserving
    mtime, a filesystem with coarse timestamps, or an editor that saved without
    updating them. This is the escape hatch.
    """
    try:
        store.initialize()
        store.conn.execute("DELETE FROM code_fts")
        store.conn.execute("DELETE FROM code_files")
        store.conn.commit()
    except Exception:
        pass
    return index_repository(store, root, **kwargs)