// WUXICODE — git working-tree probe.
//
// SCOPE: answers two questions about a checkout and nothing else:
//   1. what revision is the index at?   (HEAD sha + .git/index mtime)
//   2. where is this path's repository?
//
// It does NOT shell out to git, does not parse git's object format, and does
// not know that tool caches exist. Callers combine this with a file stamp to
// form a cache key.
//
// WHY NOT `git rev-parse HEAD`:
// A subprocess costs 5-30 ms on Windows — process creation dominates. This sits
// on the path of every cache lookup, which sits on the path of every READ tool,
// several of which run concurrently (ADR 0002). Reading .git/HEAD and one loose
// ref is two opens and two short reads, microseconds in total, and it cannot
// be defeated by a broken PATH, a locked git.exe, or a repo mid-rebase.
//
// ALWAYS SUCCEEDS. Every operation here is a probe that can fail for reasons
// entirely outside the agent's control: no repo, a detached HEAD, an unborn
// branch, a worktree whose gitdir was pruned mid-turn, a permissions error.
// None of those are exceptional. The probe reports `valid = false` or a zero
// sha and the caller continues; it never throws and never aborts a read.
//
// THREAD SAFETY: this type holds no mutable state. Every method is const and
// all probing happens in local variables, so concurrent readers need no lock.
// That is deliberate — several threads hitting the same repository is the
// normal case, not an edge case.

#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>

namespace wx::core::cache {

// Where the checkout stands. Zero means "not applicable", never "error":
//   head_sha == 0        -> no repo, detached-then-deleted, or unborn branch
//   index_mtime_ns == 0  -> no index file (a fresh `git init` has none yet)
struct GitRevision {
    std::uint64_t head_sha = 0;
    std::uint64_t index_mtime_ns = 0;
    bool valid = false;  // a repository root was located at all

    friend bool operator==(const GitRevision& a, const GitRevision& b) noexcept {
        return a.head_sha == b.head_sha && a.index_mtime_ns == b.index_mtime_ns &&
               a.valid == b.valid;
    }
};

// A file's identity for cache purposes.
//
// mtime_ns comes from std::filesystem::file_clock and its *units are
// implementation-defined* (100 ns ticks on MSVC, nanoseconds on libstdc++).
// That is safe here and nowhere else would be: the value only has to be stable
// and monotonic within one process, because the cache is in-memory and never
// outlives the run. Persisting these numbers to disk would require normalising
// to a real epoch first.
struct FileStamp {
    std::uint64_t mtime_ns = 0;
    std::uint64_t size = 0;
    bool exists = false;
    bool is_directory = false;

    friend bool operator==(const FileStamp& a, const FileStamp& b) noexcept {
        return a.mtime_ns == b.mtime_ns && a.size == b.size && a.exists == b.exists &&
               a.is_directory == b.is_directory;
    }
};

class GitProbe {
public:
    // Refuse to walk more than this many levels looking for a repository root.
    // Bounded so a path on a misconfigured network mount, or a path that never
    // terminates in anything familiar, cannot turn a cache probe into a hang.
    static constexpr int kMaxParentHops = 64;

    // Longest ref-ish file we will read. .git/HEAD is ~40 bytes; a loose ref is
    // 41. packed-refs grows with the repo but stays in the kilobytes for any
    // sane origin. The cap is a guard against reading a pathological file, not
    // an expected limit.
    static constexpr std::size_t kMaxGitFileBytes = 1u * 1024u * 1024u;

    // Walk up from `start` until a `.git` entry appears. Returns an empty path
    // when `start` is not inside a repository.
    //
    // `start` need not exist. Callers routinely probe paths for files that were
    // just deleted, and requiring existence would turn a cache miss into an
    // exception.
    [[nodiscard]] static std::filesystem::path repo_root(const std::filesystem::path& start);

    // Resolve `.git` to the actual git directory.
    //
    // Plain repositories have a `.git` directory. Linked worktrees and
    // submodules instead have a `.git` FILE holding `gitdir: <path>`. Treating
    // the file as a directory — or ignoring it — silently reports "no repo" for
    // every worktree, which would disable git-aware invalidation exactly where
    // it matters most, since a worktree is where agents do concurrent work.
    [[nodiscard]] static std::filesystem::path resolve_gitdir(const std::filesystem::path& repo_root_in);

    // HEAD commit, as a 64-bit truncation of the full hex sha.
    //
    // Resolution order:
    //   1. `.git/HEAD` holds a raw sha            -> detached HEAD
    //   2. `.git/HEAD` holds `ref: <name>` and the loose ref exists -> that sha
    //   3. `.git/HEAD` holds `ref: <name>` and only packed-refs has it -> lookup
    //   4. `.git/HEAD` exists, ref does not        -> unborn branch, sha 0
    //   5. `.git/HEAD` unreadable                  -> sha 0
    //
    // An unborn branch is a real state (`git init`, no commit yet) and must not
    // be confused with a failure: both yield 0, but callers must keep working,
    // because every file in the tree is then untracked and the index axis alone
    // still has to carry invalidation.
    [[nodiscard]] static std::uint64_t head_sha(const std::filesystem::path& gitdir);

    // Modification time of `.git/index`, in file_clock ticks.
    //
    // This is the half of the git axis that catches what HEAD cannot: staging
    // changes the index without moving HEAD, and `git checkout` moves HEAD
    // without the index's own timestamp necessarily changing first. Both axes
    // are required; either alone leaves a window where a stale read survives.
    [[nodiscard]] static std::uint64_t index_mtime(const std::filesystem::path& gitdir);

    // Combined probe. Does the repo walk and both file probes in one call so
    // callers pay for the upward walk exactly once.
    [[nodiscard]] static GitRevision revision(const std::filesystem::path& start);

    // stat a path without throwing. A missing path is `exists = false`, not an
    // error — "the file was deleted" is a state the caller must handle, and it
    // handles it identically to "the file was never there".
    [[nodiscard]] static FileStamp stamp(const std::filesystem::path& path);

    // Canonical form of a path for use as a cache-key axis.
    //
    // Per ADR 0002 rule 3: absolute, resolved, separator-normalised, and
    // case-folded on Windows. Case folding is not optional there — NTFS is
    // case-insensitive, so `README.md` and `readme.md` are the same file and
    // must not produce two cache entries that can each go stale independently.
    //
    // Resolution is lexical, not `weakly_canonical`. Following symlinks would
    // mean a filesystem walk on every key construction, and on Windows a
    // junction or a OneDrive placeholder can make that walk arbitrarily slow
    // or reparse into a network path. Lexical normalisation collapses the forms
    // that actually differ in practice — `a/./b`, `a/b/../b`, trailing
    // separators, mixed separators — without leaving the process.
    //
    // Returned as std::u8string, i.e. UTF-8, and that is not decoration:
    // path::string() converts to the system's ANSI code page, so a Korean
    // filename would come back as mojibake and then be reconstructed into a path
    // that does not exist — a cache miss on every call, for every non-ASCII
    // name, which in practice is most of them. generic_u8string() keeps the
    // bytes exact.
    [[nodiscard]] static std::u8string canonical_path(const std::filesystem::path& path);
};

// Parse up to 16 leading hex digits into a uint64. Exposed for tests.
[[nodiscard]] std::uint64_t parse_hex64(std::string_view hex) noexcept;

}  // namespace wx::core::cache