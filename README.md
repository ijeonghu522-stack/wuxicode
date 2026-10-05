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
├─ context/  assembly, budget      └─ sys/   console, proc stats
└─ ui/       brand, banner
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

## Build

Requires: Python 3.10+, CMake 3.24+, MSVC Build Tools (or GCC/Clang).

```powershell
git clone <this repo>
cd WUXICODE
pip install -e .
wuxicode --help
```

### Windows EXE

```powershell
python scripts\make_icon.py                                # assets/logo.svg -> .ico
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
.\dist\WUXICODE.exe --doctor
```

One file, ~21 MB, native core bundled, no Python required on the target
machine. Full guide: [docs/exe-build.md](docs/exe-build.md).

## Daily loop

```powershell
python scripts\daily.py                  # learn -> test -> build -> site
python scripts\daily.py --skip-build     # learn + test only
wuxicode --learn                         # just distil today's tool calls
```

Every failed `bash` call and every repeated successful one becomes a rule that
gets injected into the next turn's system prompt. `daily.py` runs the digest,
the tests, the lint rules that catch real bugs, the native rebuild when
`cpp/` changed, the EXE build, and the site sync — and writes a dated report
to `docs/reports/`.

## Testing

```powershell
pytest
ruff check python --select F,E9,B          # F=undefined/unused, E9=syntax, B=bugbear
python scripts\check_version.py            # version sources agree
python web\check_site.py                   # site structure

# C++ — needs pybind11_DIR on a standalone build, see cpp/CMakeLists.txt
cmake -S . -B build -DWX_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR=<...>
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

## Versioning

`version.json` is the single source. CMake bakes it into `_wx_core`,
`pyproject.toml` carries it for pip, and `wx/__init__.py` reads it at import.
`scripts/check_version.py` fails the build if they ever diverge.

## Status

`0.2.0`

| Phase | Scope | State |
|---|---|---|
| 1A | build + SSE stream + token counter + banner | done |
| 1B | LRU, ToolCache, GitProbe, `read` tool, tests | done |
| 2 | full tool layer, git-aware invalidation, parallel scheduler | done |
| 3 | learning layer, context assembly, budget ledger | done |
| 4 | FTXUI TUI, diff view, status bar | next |
| 5 | signed licenses, W_X ZEN gating | — |
| 6 | frozen EXE, website | done |

## 라이선스

WX Commercial License (LicenseRef-WX-Commercial)

- **WX (무료)**: 개인, 비상업적 사용
- **WX PLUS ($10/월)**: 개인, 상업적 사용
- **WX ZEN ($15/월)**: 팀, 상업적 사용
- **WX ULTIMATE ($25/월)**: API 충전 방식

자세한 내용: [LICENSE](LICENSE) · [라이선스 전략](docs/LICENSE-PLAN.md)

문의: ijeonghu522@gmail.com

---

**made by ujin**
