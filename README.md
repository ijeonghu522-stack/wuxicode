# WUXICODE

Hybrid coding agent that streams **while** your tools read.

```
wuxicode "hello"
```

---

## Why

Every existing coding agent blocks on the same thing: the model asks for a
tool result, the tool runs, the model waits, nothing moves. Three symptoms
fall out of that one design flaw.

| # | Symptom | What breaks |
|---|---|---|
| 1 | **Streaming stalls behind read tools** | The UI looks frozen during every file read |
| 2 | **Stale cache** | You edit a file, the agent re-reads the old content and rewrites it |
| 3 | **Context explosion** | Long sessions die at the context window with nothing summarized |

## The three fixes

**1 — Streaming and reads run concurrently.**
The agent loop does not wait for the model to finish before touching the disk.
Read-only tools (`read`, `grep`, `glob`, `ls`, `git_status`, `git_diff`) launch
the moment the model asks, in parallel, while tokens are still arriving.
Only their *results* get merged at the injection point. Writes never run
concurrently — see `docs/adr/0002-toolscheduler.md`.

**2 — Cache invalidation is git-aware, not timer-based.**
Cache keys carry five axes: tool name, tool schema version, canonical
arguments, `(HEAD sha, .git/index mtime)`, and the target file's
`(mtime_ns, size)`. Change one byte anywhere in that tuple and the entry is
recomputed. There is no TTL and there is no "did you mean to restart".

**3 — Context compaction is anchored, not lossy.**
Old tool results get summarized by the model with path, line ranges, and a
content hash preserved so nothing can be silently truncated. Files that are
open or pending an edit are never compacted.

## Architecture

```
wx (Python 3.10+)                 wxcore (C++20, pybind11)
├─ agent/    loop, scheduler       ├─ net/   SSE parser (byte-level state machine)
├─ llm/      providers, router     ├─ cache/ LRU, ToolCache, GitProbe
├─ tools/    registry, impls       ├─ text/  token counter
├─ context/  assembly, budget      ├─ tui/   FTXUI renderer (60fps)
└─ ui/       brand, banner         └─ sys/   console, proc stats
```

The boundary is strict: **C++ computes, Python decides.** C++ never picks a
model, never orders tool calls, never parses an OpenAI schema. See
`docs/adr/0001-sse-boundary.md`.

| Piece | Language | Why there |
|---|---|---|
| SSE parser | C++ | Correctness under arbitrary chunk boundaries is state-machine work, and it sits in the hot path of every token |
| Tool cache | C++ | mtime + git revision probes are syscall-bound; the LRU policy must be free |
| Token counter | C++ | Called on every message assembly; a Python loop there is a latency spike |
| Agent loop, tools, policy | Python | Changes often, benefits from the language's iteration speed |
| TUI | C++ (FTXUI) | 60fps repaint without per-frame allocation or GIL churn |

## Build

Requires: Python 3.10+, CMake 3.24+, MSVC Build Tools (or GCC/Clang).

```powershell
git clone <this repo>
cd WUXICODE
pip install -e .
wuxicode --help
```

## Status

`0.1.0` — Phase 1A vertical slice. Streaming path and build pipeline.
Nothing else is wired up yet.

| Phase | Scope |
|---|---|
| 1A | build + SSE stream + token counter + banner |
| 1B | LRU, ToolCache, GitProbe, `read` tool, tests |
| 2 | full tool layer, git-aware invalidation, parallel scheduler |
| 3 | context compaction, budget ledger, AGENTS.md |
| 4 | FTXUI TUI, diff view, status bar, low-spec 60fps |
| 5 | auth, signed licenses, W_X ZEN gating |
| 6 | frozen EXE, website, billing |

## Contact

`ijeonghu522@gmail.com`

---

**made by ujin**