// WUXICODE — tool result cache, keyed on five axes.
//
// SCOPE: composes LruCache (policy) and GitProbe (working-tree state) into the
// key described in the README. It does not read files and does not know what a
// tool does — callers hand it a descriptor and a value.
//
// THE FIVE AXES, and why each one is load-bearing:
//
//   1. tool name          two tools can return different bytes for one argument
//   2. schema version     a tool whose output format changes must not serve
//                         results produced by the previous format
//   3. canonical args     the literal inputs, canonicalised so that argument
//                         spelling (`./a/b` vs `a/b`) cannot split one entry
//                         into two
//   4. (HEAD sha, .git/index mtime)
//                         the working tree changed
//   5. (mtime, size) of the target file
//                         the target changed
//
// Axes 4 and 5 are what make this git-aware instead of timer-based. There is
// no TTL and no mtime threshold anywhere in this file. A file that has not
// changed is never recomputed, and a file that changed one byte ago is never
// served from cache, regardless of how fast the two events happened.
//
// BOTH GIT AXES ARE NEEDED. HEAD alone misses `git add`: staging moves the
// index without moving HEAD. The index mtime alone misses `git commit`: HEAD
// moves while the index may keep its timestamp. Either axis alone leaves a
// window in which a stale entry survives.
//
// WHY THE REPO ROOT IS RESOLVED ONCE, HERE, IN THE CONSTRUCTOR:
// Every READ tool operates inside the workspace — read_file.validate_path
// rejects anything outside it, and that confinement is the security boundary.
// So the repository containing a given file is a function of the workspace, not
// of the file. Resolving it per lookup would put a multi-level upward directory
// walk in front of every cache hit and every miss, to re-derive a constant.
// Resolved once, immutable afterward, which also keeps every method const and
// the type lock-free for readers.
//
// ORPHANED ENTRIES. Editing a file changes axis 5, so the entry describing the
// old bytes can never be hit again — correct, but it still occupies bytes until
// capacity pressure evicts it. Nothing is lost by waiting: the budget is fixed,
// so garbage is bounded no matter how long the run lasts. Callers that know a
// specific file just moved can call invalidate_path() to sweep it at once.

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "core/base/hash.hpp"
#include "core/cache/git_probe.hpp"
#include "core/cache/lru.hpp"

namespace wx::core::cache {

// Bumped when the key layout changes shape. Distinct from the per-tool schema
// version: this one says "the cache's own addressing changed", the other says
// "one tool's output format changed". Collapsing them would let a tool bump
// silently repoint unrelated tools' entries.
inline constexpr std::uint64_t kToolCacheDomain = 0x5758434143484531ULL;  // "WXCACHE1"

// Input to a cache lookup. `canonical_args` is the caller's responsibility to
// canonicalise — this type does not parse or reformat it, because only the tool
// knows what its arguments mean.
struct CacheDescriptor {
    std::string tool;                           // axis 1
    std::uint32_t schema_version = 1;           // axis 2
    std::string canonical_args;                 // axis 3

    // Subject of axis 5, already canonical via GitProbe::canonical_path.
    // Empty means "no file axis" — correct for tools whose output does not
    // depend on any single file, and required, because stat-ing "" would
    // silently produce an all-zero axis that looks like a real one.
    //
    // std::u8string, not std::string: see GitProbe::canonical_path. The bytes
    // are UTF-8 either way, so the path index below stores them verbatim and
    // loses nothing by keying on a plain std::string view of the same bytes.
    std::u8string canonical_path;                 // axis 5 subject

    // Whether to fold in the git axes. Off for results that are pure functions
    // of their arguments (a glob pattern match against an explicit file list),
    // where a git commit cannot change them and paying for the probe is waste.
    bool use_git_axis = true;

    friend bool operator==(const CacheDescriptor& a, const CacheDescriptor& b) {
        return a.tool == b.tool && a.schema_version == b.schema_version &&
               a.canonical_args == b.canonical_args &&
               a.canonical_path == b.canonical_path && a.use_git_axis == b.use_git_axis;
    }
};

struct ToolCacheStats {
    LruStats lru;
    std::uint64_t key_builds = 0;
    std::uint64_t path_invalidations = 0;   // entries removed by invalidate_path
    std::uint64_t invalidate_calls = 0;
    bool git_repo_found = false;
    std::uint64_t head_sha = 0;
    std::uint64_t index_mtime_ns = 0;
};

class ToolCache {
public:
    explicit ToolCache(std::size_t capacity_bytes = LruCache::kDefaultCapacityBytes,
                       std::filesystem::path workspace_root = std::filesystem::current_path());

    ~ToolCache() = default;

    ToolCache(const ToolCache&) = delete;
    ToolCache& operator=(const ToolCache&) = delete;
    ToolCache(ToolCache&&) = delete;
    ToolCache& operator=(ToolCache&&) = delete;

    // The cache key this descriptor currently maps to. Exposed for tests and
    // for the status bar's "why did this miss" diagnostic; the normal path uses
    // get()/put(), which build the key internally.
    [[nodiscard]] std::uint64_t key_for(const CacheDescriptor& d) const;

    // `out` is written only on a hit.
    [[nodiscard]] bool get(const CacheDescriptor& d, std::string& out);

    // Returns false only when the value exceeds the entire byte budget. Callers
    // that treat false as fatal will break on a legitimately huge file, so
    // treat it as "not cached" and carry on.
    bool put(const CacheDescriptor& d, std::string value);

    // Drop every entry belonging to one canonical path, at any axis-5 value.
    // This is the WRITE-side call: after a write lands, the pre-write reads of
    // that file are unreachable anyway, and sweeping them keeps a hot file from
    // accumulating one dead entry per save.
    //
    // Returns the number of entries removed.
    std::size_t invalidate_path(const std::u8string& canonical_path);

    // Drop every entry. The git axes do NOT need clearing on their own: they
    // are part of the key, so a commit makes the old entries unreachable by
    // construction. This is for memory reclamation and for tests.
    void clear();

    [[nodiscard]] ToolCacheStats stats() const;

    // Current working-tree state. Probed live, not stored: GitProbe holds no
    // mutable state, so this is safe to call from any thread at any time.
    [[nodiscard]] GitRevision revision() const;

    [[nodiscard]] const std::filesystem::path& workspace_root() const noexcept { return workspace_root_; }
    [[nodiscard]] const std::filesystem::path& gitdir() const noexcept { return gitdir_; }

    // Canonicalise a workspace-relative or absolute path for use as
    // CacheDescriptor::canonical_path.
    [[nodiscard]] std::u8string canonical(const std::filesystem::path& path) const;

    [[nodiscard]] std::size_t capacity_bytes() const { return cache_.capacity_bytes(); }
    void set_capacity_bytes(std::size_t bytes) { cache_.set_capacity_bytes(bytes); }

private:
    [[nodiscard]] std::uint64_t build_key(const CacheDescriptor& d) const;

    // std::hash has no standard specialisation for std::u8string, and the
    // portable alternative — hashing with the locale, or converting through a
    // narrow code page — would reintroduce the Unicode corruption the u8 path
    // type exists to prevent. Hash the raw UTF-8 bytes with the project's own
    // stable hash instead.
    struct U8Hash {
        [[nodiscard]] std::size_t operator()(const std::u8string& s) const noexcept {
            return static_cast<std::size_t>(
                wx::core::hash64(std::string_view(reinterpret_cast<const char*>(s.data()), s.size())));
        }
    };

    // Caller must hold index_mutex_.
    void index_insert_locked(const std::u8string& canonical_path, std::uint64_t key);
    void index_remove_locked(const std::u8string& canonical_path, std::uint64_t key);

    LruCache cache_;
    std::filesystem::path workspace_root_;
    std::filesystem::path gitdir_;

    // Lock ordering: index_mutex_ is always taken BEFORE cache_'s internal
    // mutex, never after. Invalidation takes index_mutex_, collects keys, then
    // calls into the LRU. If the order ever inverted, a concurrent put() could
    // take the two locks in the opposite sequence and deadlock. Nothing in this
    // file may call a cache_ method while holding index_mutex_ *after* it has
    // been released by cache_, and no path takes cache_'s lock and then
    // index_mutex_.
    mutable std::mutex index_mutex_;

    // canonical path -> the keys currently stored for it.
    std::unordered_map<std::u8string, std::unordered_set<std::uint64_t>, U8Hash> path_index_;

    // mutable so the const lookup paths can bump counters without a second
    // mutable accessor on every field.
    mutable std::atomic<std::uint64_t> key_builds_{0};
    mutable std::atomic<std::uint64_t> invalidate_calls_{0};
    mutable std::atomic<std::uint64_t> path_invalidations_{0};
};

}  // namespace wx::core::cache