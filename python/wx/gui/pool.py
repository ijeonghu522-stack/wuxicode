"""One agent per tab.

The window shows several tabs, and two of them asking questions at once used
to be the same conversation. That is not a cosmetic problem: two turns sharing
one AgentLoop interleave their deltas into one message, and the answer to
"what did this file contain" ends up attributed to the question about the
other file.

So the server owns a pool keyed by tab id, and every request names the tab it
belongs to. A pool, not a map, because tabs are created and closed constantly
and a map keyed by an integer the browser invented would leak a live agent
forever.

Sessions are created lazily and evicted when idle, so opening twenty tabs and
closing nineteen leaves one agent in memory rather than twenty.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wx.gui.session import Session

#: A tab that has been closed but whose agent is still shutting down. Bounded
#: so a user who opens and closes tabs in a loop cannot grow this without end.
MAX_TRACKED = 64


@dataclass
class SessionPool:
    """Sessions keyed by tab, sharing one workspace and one server thread set."""

    workspace: Path

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self._sessions: dict[int, Session] = {}
        self._order: list[int] = []
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------

    def get(self, tab: int) -> Session:
        """The session for `tab`, created on first use.

        Synchronised on a lock held across AgentLoop construction, which reads
        config and builds tool objects. That is a few milliseconds; doing it
        twice concurrently for the same tab would leave one agent unreachable
        and still burning a thread.
        """
        with self._lock:
            s = self._sessions.get(tab)
            if s is not None:
                return s
            s = Session(workspace=self.workspace)
            self._sessions[tab] = s
            if tab in self._order:
                self._order.remove(tab)
            self._order.append(tab)
            self._evict_locked()
            return s

    def peek(self, tab: int) -> Session | None:
        with self._lock:
            return self._sessions.get(tab)

    def close(self, tab: int) -> bool:
        """Drop a tab's agent. False when there was nothing to drop."""
        with self._lock:
            s = self._sessions.pop(tab, None)
            if tab in self._order:
                self._order.remove(tab)
        if s is None:
            return False
        s.shutdown()
        return True

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._order.clear()
        for s in sessions:
            s.shutdown()

    def _evict_locked(self) -> None:
        while len(self._order) > MAX_TRACKED:
            oldest = self._order[0]
            s = self._sessions.pop(oldest, None)
            self._order.pop(0)
            if s is not None and not s.busy():
                s.shutdown()
            elif s is not None:
                # Busy: put it back rather than kill a running turn. The list
                # is allowed to exceed MAX_TRACKED in that case; the bound is a
                # memory hint, not a correctness requirement.
                self._sessions[oldest] = s
                self._order.append(oldest)
                return

    # -- convenience ------------------------------------------------------

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._sessions)


# ---------------------------------------------------------------------------
# file reading
# ---------------------------------------------------------------------------

#: Refuse to inline anything larger than this. A minified bundle or a lock file
#: in the editor is unreadable and would freeze the render loop.
MAX_VIEW_BYTES = 512 * 1024

#: Extensions the viewer will not try to show as text.
BINARY_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".pyd", ".so", ".dylib", ".a", ".lib", ".o", ".obj",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".pkl", ".npy", ".npz", ".pt", ".pth",
    ".gguf", ".safetensors", ".bin", ".onnx",
})

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".markdown": "markdown", ".txt": "text",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".rs": "rust", ".go": "go", ".java": "java", ".rb": "ruby",
    ".sh": "shell", ".ps1": "powershell", ".bat": "batch",
    ".sql": "sql", ".xml": "xml", ".svg": "xml", ".ini": "ini",
    ".cfg": "ini", ".psd1": "powershell", ".bat.txt": "text",
}


def _resolve_in(root: Path, rel: str) -> Path | None:
    """Absolute path for a workspace-relative path, or None if it escapes.

    The server hands bytes to a process that runs shell tools. Without this the
    file panel is a way to read C:\\Users\\...\\.ssh\\id_rsa.
    """
    try:
        target = (root / rel).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target


def read_file(root: Path, rel: str) -> dict[str, Any]:
    """One file, as the viewer needs it. Never raises."""
    out: dict[str, Any] = {"path": rel, "ok": False}
    target = _resolve_in(root, rel)
    if target is None:
        out["error"] = "경로가 작업 디렉터리 밖입니다"
        return out
    if not target.is_file():
        out["error"] = "파일이 아닙니다"
        return out
    try:
        size = target.stat().st_size
    except OSError as exc:
        out["error"] = str(exc)
        return out
    if size > MAX_VIEW_BYTES:
        out["error"] = f"파일이 너무 큽니다 ({size // 1024}KB, 한도 {MAX_VIEW_BYTES // 1024}KB)"
        out["size"] = size
        return out
    if target.suffix.lower() in BINARY_EXT:
        out["error"] = "바이너리 파일입니다"
        out["size"] = size
        return out

    try:
        raw = target.read_bytes()
    except OSError as exc:
        out["error"] = str(exc)
        return out

    # A NUL in the first block is the cheapest reliable "this is not text"
    # test, and it is what git uses.
    if b"\0" in raw[:8192]:
        out["error"] = "바이너리 파일입니다"
        out["size"] = size
        return out

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")  # Korean Windows default
        except UnicodeDecodeError:
            out["error"] = "텍스트로 디코딩할 수 없습니다"
            return out

    out.update(
        ok=True,
        size=size,
        text=text,
        lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
        lang=LANG_BY_EXT.get(target.suffix.lower(), ""),
        truncated=size >= MAX_VIEW_BYTES,
    )
    return out


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str, timeout: float = 8.0) -> tuple[int, str]:
    """Run git in `root`. Returns (returncode, output).

    Not found on PATH is a return code, not an exception: a workspace without
    git is a normal state and the diff panel should say so rather than take
    the window down.
    """
    try:
        p = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=timeout,
            # Windows Git otherwise opens a pager and blocks forever on
            # anything with more output than a screen.
            env={**os.environ, "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except FileNotFoundError:
        return 127, "git not found"
    except subprocess.TimeoutExpired:
        return 124, "git timed out"
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out if not err else (out + "\n" + err).strip()


def git_status(root: Path) -> dict[str, Any]:
    """Branch, upstream, and the porcelain change list."""
    code, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return {"repo": False, "branch": "", "changes": [], "staged": 0, "unstaged": 0}

    _c, sha = _git(root, "rev-parse", "--short", "HEAD")
    _c, upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    changes: list[dict[str, Any]] = []
    staged = unstaged = 0
    code, out = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if code == 0:
        for line in out.splitlines():
            if len(line) < 4:
                continue
            x, y = line[0], line[1]
            path = line[3:].strip()
            # A rename is "old -> new"; the new path is the one that exists.
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changes.append({"path": path, "x": x, "y": y})
            if x not in (" ", "?"):
                staged += 1
            if y not in (" ", "?"):
                unstaged += 1

    return {
        "repo": True,
        "branch": branch.strip(),
        "sha": sha.strip(),
        "upstream": upstream.strip() if code == 0 else "",
        "changes": changes,
        "staged": staged,
        "unstaged": unstaged,
    }


def git_diff(root: Path, path: str | None = None, staged: bool = False) -> dict[str, Any]:
    """Unified diff, either the whole tree or one path."""
    args = ["diff", "--no-color", "--no-ext-diff", "-U3"]
    if staged:
        args.append("--cached")
    if path:
        target = _resolve_in(root, path)
        if target is None:
            return {"ok": False, "error": "경로가 작업 디렉터리 밖입니다", "diff": ""}
        args += ["--", str(target)]
    else:
        args += ["--", "."]

    code, out = _git(root, *args)
    if code == 127:
        return {"ok": False, "error": "git not found", "diff": ""}
    if code != 0:
        return {"ok": False, "error": out.strip() or f"git diff failed ({code})", "diff": ""}
    return {"ok": True, "error": "", "diff": out}


def git_log(root: Path, limit: int = 30) -> dict[str, Any]:
    """Recent commits, one line each."""
    code, out = _git(
        root, "log", f"-{max(1, min(limit, 200))}",
        "--pretty=format:%h\x1f%an\x1f%ar\x1f%s",
    )
    if code != 0:
        return {"ok": False, "commits": []}
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1], "when": parts[2], "subject": parts[3]})
    return {"ok": True, "commits": commits}
