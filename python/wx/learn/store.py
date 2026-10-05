"""WUXICODE — learn store.

The learning substrate. One SQLite file, FTS5 for retrieval, no vector
database and no embedding model. That is a deliberate trade: an embedding
index costs a model download, a warm-up pass over the whole tree, and a
dependency, and the retrieval quality it buys over a well-chosen tokenizer on
a code-shaped corpus is not worth it. FTS5 is in the standard library on
Python 3.11+ and it answers the question this system actually asks — "have I
been burned by this exact thing before" — in microseconds.

TOKENIZER: trigram, not unicode61. unicode61 splits on non-alphanumeric
characters, and Hangul syllables are alphanumeric, so a Korean sentence becomes
one enormous token that matches nothing but itself. trigram indexes overlapping
three-character windows, which turns Korean substring search into ordinary FTS
matching. It costs more index space and buys correct behaviour on the corpus
this product is actually written in.

WHAT IS STORED, and why each table exists:

    sessions    one row per CLI run. Groups everything else.
    turns       user and assistant messages. The raw signal.
    tool_calls  every tool invocation with its arguments and outcome.
                This is the part that carries accept/reject information.
    observations  distilled knowledge — conventions, preferences,
                antipatterns. What `extract.py` writes and `inject.py` reads.
    code_fts    repository text, chunked by file, for retrieval.
    meta        schema version and bookkeeping.

CONCURRENCY: WAL mode, one connection per thread. The agent loop is async and
the digest runs in a separate process, so a plain sqlite3 connection shared
across both would eventually produce "database is locked" at the worst possible
moment. WAL lets a reader and a writer coexist.

The store never raises on corruption. A learning layer that can take down the
agent it is supposed to help is worse than no learning layer, so every public
entry point degrades to a no-op rather than propagating.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = ["LearnStore", "Observation", "ToolRecord", "default_store_path", "open_store"]

SCHEMA_VERSION = 1

# Directories never worth indexing. Walking them wastes time and fills the
# index with content that cannot inform a code decision.
_SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "node_modules", ".venv", "venv", "env", ".idea", ".vscode",
        "build", "dist", ".eggs", "site-packages", ".tox", ".next", ".cache",
    }
)

# Extension -> language label. Unknown extensions are indexed under their raw
# suffix so a `.wx` or `.tql` file is still findable, just not labelled.
_LANGS = {
    ".py": "python", ".pyi": "python", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".h": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".c": "c",
    ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".swift": "swift",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".rst": "markdown", ".txt": "text",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".bat": "bat",
    ".sql": "sql", ".cmake": "cmake", ".ipynb": "notebook",
}

# Files above this are skipped. A generated bundle is not context.
MAX_INDEX_FILE_BYTES = 512 * 1024


def default_store_path() -> Path:
    """Where the store lives unless told otherwise.

    `%LOCALAPPDATA%` on Windows, `~/.local/state` elsewhere — the same
    XDG-ish split the auth provider store already uses. Kept next to the other
    per-user state rather than inside any repository: a learning store that
    travels with a checkout would be silently duplicated per clone.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "wuxicode" / "learn.db"


def _fts_tokenizer(conn: sqlite3.Connection) -> str:
    """Pick trigram when the linked SQLite is new enough, else unicode61.

    trigram landed in SQLite 3.34. Python 3.10 can be built against anything
    from 3.8 upward, so this is a runtime check rather than a version
    assumption.
    """
    version = sqlite3.sqlite_version_info
    if version >= (3, 34, 0):
        return "trigram"
    return "unicode61"


class Observation:
    """One distilled piece of knowledge.

    `kind` drives how it is phrased in the prompt and how the digest groups it:

        convention   a fact about this repository ("한국어 주석을 쓴다")
        preference   how this user wants to be answered ("소문자로 말해")
        antipattern  something that failed before and should not be repeated
        fact         an environment fact ("Kaggle RAM 은 29GB")
    """

    __slots__ = ("id", "ts", "kind", "text", "source", "hits")

    def __init__(
        self,
        id: int,
        ts: float,
        kind: str,
        text: str,
        source: str,
        hits: int = 0,
    ) -> None:
        self.id = id
        self.ts = ts
        self.kind = kind
        self.text = text
        self.source = source
        self.hits = hits

    @property
    def age_days(self) -> float:
        return (time.time() - self.ts) / 86400.0

    def score(self, *, now: float | None = None) -> float:
        """Ranking weight.

        Recency decays over roughly two weeks and use count compounds. A rule
        the agent has followed forty times outranks a rule recorded yesterday,
        but a rule from last month does not outrank a fresh one — the
        repository moves, and stale rules mislead more than they help.
        """
        now = now if now is not None else time.time()
        age_days = max(0.0, (now - self.ts) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 14.0)
        return recency * (1.0 + 0.15 * min(self.hits, 40))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "hits": self.hits,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Observation({self.kind}, hits={self.hits}, {self.text[:40]!r})"


class ToolRecord:
    """One tool invocation and what came of it."""

    __slots__ = ("turn_id", "name", "args", "result", "ok", "ts", "session_id")

    def __init__(
        self,
        turn_id: int | None,
        name: str,
        args: str,
        result: str,
        ok: bool,
        ts: float,
        session_id: str | None = None,
    ) -> None:
        self.turn_id = turn_id
        self.name = name
        self.args = args
        self.result = result
        self.ok = ok
        self.ts = ts
        self.session_id = session_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "name": self.name,
            "args": self.args,
            "result": self.result,
            "ok": self.ok,
            "ts": self.ts,
            "session_id": self.session_id,
        }


_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id        TEXT PRIMARY KEY,
    started   REAL NOT NULL,
    ended     REAL,
    cwd       TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns(ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id    INTEGER,
    session_id TEXT,
    ts         REAL NOT NULL,
    name       TEXT NOT NULL,
    args       TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_tools_ts   ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tools_name ON tool_calls(name, ts);

CREATE TABLE IF NOT EXISTS observations (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    kind   TEXT NOT NULL,
    text   TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    hits   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(kind, text)
);
CREATE INDEX IF NOT EXISTS idx_obs_ts   ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_kind ON observations(kind);

CREATE TABLE IF NOT EXISTS code_files (
    path   TEXT PRIMARY KEY,
    mtime  REAL NOT NULL,
    size   INTEGER NOT NULL,
    lang   TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS obs_fts  USING fts5(kind UNINDEXED, body, tokenize='{tok}');
CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(path UNINDEXED, lang UNINDEXED, body, tokenize='{tok}');
"""


class LearnStore:
    """SQLite-backed learning substrate.

    One instance per process is normal; `open_store` returns a process-wide
    singleton keyed by path so the agent loop and a CLI subcommand cannot end
    up pointing at two different files.

    Thread-safe. Each thread gets its own connection because sqlite3
    connections are not shareable across threads by default and the agent loop
    drives tools from a worker pool.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False

    # -- connection ---------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def initialize(self) -> None:
        """Create the schema. Idempotent, and safe to race."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            tok = _fts_tokenizer(self.conn)
            self.conn.executescript(_SCHEMA_TEMPLATE.format(tok=tok))
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
            self._initialized = True

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        conn = self.conn
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        conn.commit()

    def healthy(self) -> bool:
        """True when the store is usable. Never raises."""
        try:
            self.initialize()
            self.conn.execute("SELECT 1 FROM observations LIMIT 1").fetchone()
            return True
        except Exception:
            return False

    def tokenizer(self) -> str:
        return _fts_tokenizer(self.conn)

    # -- sessions -----------------------------------------------------------

    def start_session(self, session_id: str, cwd: str | None = None) -> None:
        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions(id, started, cwd) VALUES(?,?,?)",
                    (session_id, time.time(), cwd),
                )
        except Exception:
            pass

    def end_session(self, session_id: str, turn_count: int) -> None:
        try:
            with self._tx() as conn:
                conn.execute(
                    "UPDATE sessions SET ended=?, turn_count=? WHERE id=?",
                    (time.time(), turn_count, session_id),
                )
        except Exception:
            pass

    # -- turns --------------------------------------------------------------

    def record_turn(self, session_id: str, role: str, content: str) -> int:
        """Store one message. Returns the row id, or 0 if the store is down."""
        try:
            with self._tx() as conn:
                cur = conn.execute(
                    "INSERT INTO turns(session_id, ts, role, content) VALUES(?,?,?,?)",
                    (session_id, time.time(), role, content),
                )
                return int(cur.lastrowid or 0)
        except Exception:
            return 0

    def recent_turns(self, limit: int = 20, session_id: str | None = None) -> list[dict[str, Any]]:
        try:
            self.initialize()
            if session_id:
                rows = self.conn.execute(
                    "SELECT id, session_id, ts, role, content FROM turns "
                    "WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id, session_id, ts, role, content FROM turns "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in reversed(rows)]
        except Exception:
            return []

    # -- tool calls ---------------------------------------------------------

    def record_tool_call(
        self,
        *,
        name: str,
        args: str,
        result: str,
        ok: bool,
        turn_id: int | None = None,
        session_id: str | None = None,
    ) -> int:
        try:
            with self._tx() as conn:
                cur = conn.execute(
                    "INSERT INTO tool_calls(turn_id, session_id, ts, name, args, result, ok) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (turn_id, session_id, time.time(), name, args[:8000], result[:8000], int(ok)),
                )
                return int(cur.lastrowid or 0)
        except Exception:
            return 0

    def tool_calls_since(self, since: float, limit: int = 500) -> list[ToolRecord]:
        try:
            self.initialize()
            rows = self.conn.execute(
                "SELECT turn_id, session_id, ts, name, args, result, ok FROM tool_calls "
                "WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (since, limit),
            ).fetchall()
            return [
                ToolRecord(
                    turn_id=r["turn_id"],
                    name=r["name"],
                    args=r["args"],
                    result=r["result"],
                    ok=bool(r["ok"]),
                    ts=r["ts"],
                    session_id=r["session_id"],
                )
                for r in reversed(rows)
            ]
        except Exception:
            return []

    def repeated_failures(self, window_days: float = 7.0) -> list[tuple[str, str, int]]:
        """Tool calls that failed more than once, grouped by name+args.

        This is the raw material for antipattern extraction. Grouping on the
        argument blob as well as the name matters: `bash("pytest")` failing
        twice is noise, `bash("npm run build")` failing twice is a broken build
        step worth remembering.
        """
        try:
            self.initialize()
            since = time.time() - window_days * 86400.0
            rows = self.conn.execute(
                "SELECT name, args, COUNT(*) AS n FROM tool_calls "
                "WHERE ok=0 AND ts >= ? GROUP BY name, args HAVING n >= 2 "
                "ORDER BY n DESC LIMIT 50",
                (since,),
            ).fetchall()
            return [(r["name"], r["args"], r["n"]) for r in rows]
        except Exception:
            return []

    def successful_patterns(self, window_days: float = 7.0) -> list[tuple[str, str, int]]:
        """Tool calls that succeeded repeatedly — the recipes worth reusing."""
        try:
            self.initialize()
            since = time.time() - window_days * 86400.0
            rows = self.conn.execute(
                "SELECT name, args, COUNT(*) AS n FROM tool_calls "
                "WHERE ok=1 AND ts >= ? GROUP BY name, args HAVING n >= 2 "
                "ORDER BY n DESC LIMIT 50",
                (since,),
            ).fetchall()
            return [(r["name"], r["args"], r["n"]) for r in rows]
        except Exception:
            return []

    # -- observations -------------------------------------------------------

    def add_observation(self, kind: str, text: str, source: str = "") -> int:
        """Insert a distilled fact. Duplicate (kind, text) is a no-op.

        The uniqueness constraint is what stops the daily digest from growing
        the prompt by re-discovering the same rule every morning. An existing
        observation is refreshed rather than duplicated so its timestamp — and
        therefore its recency score — reflects the latest confirmation.
        """
        text = " ".join(text.split())
        if not text:
            return 0
        try:
            with self._tx() as conn:
                existing = conn.execute(
                    "SELECT id FROM observations WHERE kind=? AND text=?", (kind, text)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE observations SET ts=?, source=? WHERE id=?",
                        (time.time(), source, existing["id"]),
                    )
                    return int(existing["id"])
                cur = conn.execute(
                    "INSERT INTO observations(ts, kind, text, source) VALUES(?,?,?,?)",
                    (time.time(), kind, text, source),
                )
                obs_id = int(cur.lastrowid or 0)
                conn.execute(
                    "INSERT INTO obs_fts(kind, body) VALUES(?,?)", (kind, text)
                )
                return obs_id
        except Exception:
            return 0

    def add_observations(self, items: list[tuple[str, str]], source: str = "") -> int:
        """Bulk insert. Returns how many were newly created."""
        added = 0
        for kind, text in items:
            if self.add_observation(kind, text, source):
                added += 1
        return added

    def observations(
        self,
        *,
        kind: str | None = None,
        limit: int = 200,
        query: str | None = None,
    ) -> list[Observation]:
        """Observations, ranked by score.

        With `query`, FTS narrows the candidate set first and the score still
        applies — the point of retrieval here is to drop irrelevant rules, not
        to reorder relevant ones.
        """
        try:
            self.initialize()
            if query and query.strip():
                escaped = _fts_query(query)
                if escaped:
                    try:
                        rows = self.conn.execute(
                            "SELECT o.id, o.ts, o.kind, o.text, o.source, o.hits "
                            "FROM obs_fts f JOIN observations o ON o.id = f.rowid "
                            "WHERE obs_fts MATCH ? "
                            + ("AND o.kind = ? " if kind else "")
                            + "ORDER BY bm25(obs_fts) LIMIT ?",
                            ((escaped, kind) if kind else (escaped, limit)),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                    if rows:
                        return self._rank(rows, limit)
            if kind:
                rows = self.conn.execute(
                    "SELECT id, ts, kind, text, source, hits FROM observations "
                    "WHERE kind=? ORDER BY ts DESC LIMIT ?",
                    (kind, limit * 4),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id, ts, kind, text, source, hits FROM observations "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit * 4,),
                ).fetchall()
            return self._rank(rows, limit)
        except Exception:
            return []

    def _rank(self, rows: list[sqlite3.Row], limit: int) -> list[Observation]:
        now = time.time()
        obs = [
            Observation(
                id=r["id"], ts=r["ts"], kind=r["kind"], text=r["text"],
                source=r["source"], hits=r["hits"],
            )
            for r in rows
        ]
        obs.sort(key=lambda o: o.score(now=now), reverse=True)
        return obs[:limit]

    def mark_used(self, ids: list[int]) -> None:
        """Bump the hit counter for observations that were injected.

        This is the feedback edge: a rule that keeps getting selected earns
        its place, one that never does decays out of the ranked set on its own.
        """
        if not ids:
            return
        try:
            with self._tx() as conn:
                conn.executemany(
                    "UPDATE observations SET hits = hits + 1 WHERE id = ?",
                    [(i,) for i in ids],
                )
        except Exception:
            pass

    def prune(self, keep_days: float = 180.0) -> int:
        """Drop observations older than the horizon. Returns rows removed."""
        try:
            with self._tx() as conn:
                cutoff = time.time() - keep_days * 86400.0
                ids = [
                    r["id"]
                    for r in conn.execute("SELECT id FROM observations WHERE ts < ?", (cutoff,))
                    .fetchall()
                ]
                if not ids:
                    return 0
                marks = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM observations WHERE id IN ({marks})", ids)
                conn.execute(f"DELETE FROM obs_fts WHERE rowid IN ({marks})", ids)
                conn.execute("DELETE FROM turns WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM tool_calls WHERE ts < ?", (cutoff,))
            return len(ids)
        except Exception:
            return 0

    #: Counters reported by `stats`. Always present, zero-filled when the store
    #: cannot be opened, so a caller can index them without a guard. A dead store
    #: used to return a two-key dict and every `stats()["turns"]` raised
    #: KeyError -- which is exactly the code path a status line takes when
    #: something is already broken.
    _COUNT_KEYS = (
        "sessions",
        "turns",
        "tool_calls",
        "observations",
        "code_files",
    )

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": str(self.path),
            "tokenizer": None,
            "schema_version": SCHEMA_VERSION,
            "size_bytes": 0,
        }
        out.update({key: 0 for key in self._COUNT_KEYS})
        try:
            self.initialize()
            c = self.conn
            out["tokenizer"] = self.tokenizer()
            for key, table in (
                ("sessions", "sessions"),
                ("turns", "turns"),
                ("tool_calls", "tool_calls"),
                ("observations", "observations"),
                ("code_files", "code_files"),
            ):
                out[key] = c.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            out["size_bytes"] = self.path.stat().st_size if self.path.exists() else 0
        except Exception:
            out["error"] = "unavailable"
        return out


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    FTS5 has its own query grammar and raw user text routinely contains the
    characters that mean something in it — quotes, `*`, `NEAR`, `-`. Wrapping
    each whitespace-separated fragment in double quotes makes it a literal
    phrase and removes the whole class of syntax errors. Fragments shorter than
    a trigram cannot match anything, so they are dropped rather than quoted.
    """
    parts: list[str] = []
    for frag in raw.split():
        frag = frag.strip('"')
        if len(frag) >= 3:
            parts.append('"' + frag.replace('"', '""') + '"')
    return " ".join(parts)


# -- process-wide registry ---------------------------------------------------

_stores: dict[str, LearnStore] = {}
_registry_lock = threading.Lock()


def open_store(path: str | Path | None = None) -> LearnStore:
    """Return the shared store for `path` (or the default), creating it once."""
    resolved = Path(path) if path is not None else default_store_path()
    key = str(resolved.resolve() if resolved.parent.exists() else resolved)
    with _registry_lock:
        store = _stores.get(key)
        if store is None:
            store = LearnStore(resolved)
            _stores[key] = store
        return store