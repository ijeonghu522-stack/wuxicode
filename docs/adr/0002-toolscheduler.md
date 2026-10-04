# ADR 0002 — ToolScheduler concurrency rules

- Status: accepted
- Date: 2026-06
- Applies to: `python/wx/agent/scheduler.py` (Phase 2), `python/wx/tools/registry.py`
- Not implemented in Phase 1A. Frozen here so Phase 2 implements the rules
  rather than inventing them and calling it done.

## Context

WUXICODE's first differentiator is that reads run while tokens are still
streaming, instead of after the model finishes. That buys latency, and it
introduces a class of bug the sequential model never had: a read observing a
file mid-write.

The naive implementation — run everything as it arrives — produces a tool
reading a file that a `write` in the same turn has already truncated, and
returning empty content that the model then reasons about confidently.

## Decision

### Tool classification

Every registered tool declares its class. The declaration lives next to the
tool's JSON schema in its own module, because a tool whose concurrency class is
declared somewhere else will be declared wrongly.

**READ** — no filesystem or process mutation:

| tool | notes |
|---|---|
| `read` | |
| `grep` | |
| `glob` | |
| `ls` | |
| `git_status` | |
| `git_diff` | |

**WRITE** — mutates filesystem or process state:

| tool | notes |
|---|---|
| `write` | |
| `edit` | |
| `shell` | arbitrary; classified as WRITE regardless of the command |
| `apply_patch` | |

`shell` is WRITE unconditionally. A command string cannot be classified
reliably and a heuristic that guesses wrong corrupts the run.

### Rules

1. **A WRITE runs only after the previous turn has fully completed.**
   No write starts while model output for the current turn is still arriving.

2. **No READ starts while a WRITE is executing** (barrier).
   The scheduler holds a global barrier across the whole write set, not per
   path. A file-level lock is a false economy: a build via `shell` can touch
   anything.

3. **A WRITE waits for any READ in flight on the same canonical path.**
   The path key is the canonical form: absolute, resolved, separator-normalised,
   case-folded on Windows. Both rules 2 and 3 can hold; the stricter wins.

4. **READ failures are absorbed into the result string; WRITE failures abort
   immediately and report a rollback notice.**
   A failed read is information the model can reason about — a file that moved,
   a path that does not exist. A failed write has already left the filesystem
   in a state the model does not know about, so continuing would let it reason
   from a false picture.

### Prefetch scope

Prediction is limited to four signals, all of which come from work already in
flight:

- the previous turn's tool-call pattern
- the set of open files
- the set of paths named in the current `git diff`
- files named in the model's own text

Explicitly forbidden:

- scanning the project tree speculatively
- walking a directory to guess what might be read next
- any prefetch that reads bytes not requested by a signal above

Prefetch may only issue READ-class tools. There is no speculative write under
any signal.

## Consequences

**Accepted costs**

- Reads and writes never overlap, so a turn that edits five files serialises
  them. That is the point. A parallel writer is not faster if it produces
  wrong output often enough to be re-run.
- `shell` as a permanent barrier will need a narrow escape in Phase 2 for
  read-only shell commands (`git log`, `grep`). That escape must be an
  explicit opt-in per tool declaration, never a heuristic on the command string.
  It does not exist yet.

**Rejected alternatives**

| Option | Why not |
|---|---|
| Run everything in arrival order | The original bug. A read can observe a half-written file |
| Optimistic concurrency: read first, verify on write | Correct in theory, but a failure mid-batch leaves a partially applied edit set with no clean rollback story. Worse than serialising |
| Per-path locking only | A `shell` invocation can write any path. Per-path locking gives false safety |
| Snapshot the tree and read from it | Memory cost scales with the repo, and the snapshot can already be stale by the time the model acts on it |
| Speculative prefetch over the project tree | Turns a 40 ms read into a 2 s directory walk on every turn, on exactly the low-spec machines this project targets |

## Verification (Phase 2 exit criteria)

1. Five READ tools dispatched together execute concurrently — wall time close
   to the slowest, not the sum.
2. A WRITE never begins while a READ is in flight (barrier asserted in test).
3. A READ of the same path issued during a WRITE is deferred, not rejected.
4. A READ that raises is delivered to the model as text; the turn continues.
5. A WRITE that raises stops the turn and emits a rollback notice.
6. A tool with an undeclared class fails to register. There is no default.