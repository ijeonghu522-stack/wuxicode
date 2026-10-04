// WUXICODE — five-axis tool result cache. See tool_cache.hpp for scope.

#include "core/cache/tool_cache.hpp"

#include <utility>

namespace wx::core::cache {

namespace fs = std::filesystem;

namespace {

// Mix a uint64 axis into the running accumulator. Kept separate from the string
// axes so a numeric 0 (an unborn branch, a missing index) cannot be confused
// with an absent field.
constexpr std::uint64_t mix_axis(std::uint64_t acc, std::uint64_t value) noexcept {
    return wx::core::hash64_combine(value, acc);
}

constexpr std::uint64_t mix_text(std::uint64_t acc, std::string_view text) noexcept {
    return wx::core::hash64_combine(wx::core::hash64(text), acc);
}

}  // namespace

ToolCache::ToolCache(std::size_t capacity_bytes, fs::path workspace_root)
    : cache_(capacity_bytes), workspace_root_(std::move(workspace_root)) {
    std::error_code ec;
    const fs::path abs_root =
        workspace_root_.is_absolute() ? workspace_root_ : fs::absolute(workspace_root_, ec);
    if (!ec && !abs_root.empty()) {
        workspace_root_ = abs_root.lexically_normal();
    }

    // Resolved once, then immutable — see the header. A workspace that is not a
    // repository is normal (a plain script directory, a temp fixture in the
    // tests) and leaves gitdir_ empty, which the key builder handles by mixing
    // a no-repo marker.
    const fs::path repo_root = GitProbe::repo_root(workspace_root_);
    if (!repo_root.empty()) {
        gitdir_ = GitProbe::resolve_gitdir(repo_root);
    }
}

std::uint64_t ToolCache::build_key(const CacheDescriptor& d) const {
    // Domain separation: these keys must never collide with a bare hash64() of
    // the same bytes used anywhere else for a different purpose.
    std::uint64_t acc = wx::core::hash64_combine(kToolCacheDomain, wx::core::kFnvOffsetBasis);

    acc = mix_text(acc, d.tool);
    acc = mix_axis(acc, static_cast<std::uint64_t>(d.schema_version));
    acc = mix_text(acc, d.canonical_args);

    // Axis 4 — working tree. Probed live every time. This costs two file reads
    // and a stat, and it is the entire reason the cache is trustworthy, so it
    // is not memoised: a memo would need its own invalidation rule, which is the
    // bug this design exists to remove.
    if (d.use_git_axis) {
        const GitRevision rev = GitProbe::revision(workspace_root_);
        acc = mix_axis(acc, rev.valid ? rev.head_sha : 0u);
        acc = mix_axis(acc, rev.index_mtime_ns);
        // `valid` is folded in separately so that "no repo" (all zeros) and
        // "repo with an unborn branch and no index yet" (also all zeros) are
        // distinguishable keys.
        acc = mix_axis(acc, rev.valid ? 1u : 2u);
    } else {
        acc = mix_axis(acc, 0xFFFFFFFFFFFFFFFFULL);  // distinct from any git state
    }

    // Axis 5 — the subject file. Probed from the canonical path, which is
    // absolute, so the stamp does not depend on the process's current
    // directory at the moment of the call.
    if (!d.canonical_path.empty()) {
        // Construct from the u8string directly. u8path() is deprecated in C++20
        // and, more to the point, the path is already UTF-8 — reconstructing it
        // through the ANSI code page would break every non-ASCII filename.
        const fs::path p(d.canonical_path);
        const FileStamp st = GitProbe::stamp(p);
        acc = mix_axis(acc, st.mtime_ns);
        acc = mix_axis(acc, st.size);
        acc = mix_axis(acc, st.exists ? 1u : 0u);
        acc = mix_axis(acc, st.is_directory ? 1u : 0u);
    } else {
        acc = mix_axis(acc, 0xFFFFFFFFFFFFFFFEULL);
    }

    return acc;
}

std::uint64_t ToolCache::key_for(const CacheDescriptor& d) const {
    key_builds_.fetch_add(1, std::memory_order_relaxed);
    return build_key(d);
}

bool ToolCache::get(const CacheDescriptor& d, std::string& out) {
    key_builds_.fetch_add(1, std::memory_order_relaxed);
    return cache_.get(build_key(d), out);
}

bool ToolCache::put(const CacheDescriptor& d, std::string value) {
    key_builds_.fetch_add(1, std::memory_order_relaxed);
    const std::uint64_t key = build_key(d);

    // Index first, then the LRU. If the LRU refuses the value (it exceeds the
    // whole budget) the index entry below is corrected on the same path, so the
    // index never accumulates a key that maps to nothing.
    if (!d.canonical_path.empty()) {
        std::lock_guard<std::mutex> lock(index_mutex_);
        index_insert_locked(d.canonical_path, key);
    }

    const bool stored = cache_.put(key, std::move(value));
    if (!stored && !d.canonical_path.empty()) {
        std::lock_guard<std::mutex> lock(index_mutex_);
        index_remove_locked(d.canonical_path, key);
    }
    return stored;
}

void ToolCache::index_insert_locked(const std::u8string& canonical_path, std::uint64_t key) {
    path_index_[canonical_path].insert(key);
}

void ToolCache::index_remove_locked(const std::u8string& canonical_path, std::uint64_t key) {
    const auto it = path_index_.find(canonical_path);
    if (it == path_index_.end()) {
        return;
    }
    it->second.erase(key);
    if (it->second.empty()) {
        // Leaving the empty set behind would grow this map without bound on a
        // long session that touches many files, since every path ever written
        // would keep a bucket.
        path_index_.erase(it);
    }
}

std::size_t ToolCache::invalidate_path(const std::u8string& canonical_path) {
    invalidate_calls_.fetch_add(1, std::memory_order_relaxed);

    // Collect under the index lock, erase outside it. cache_.erase takes the
    // LRU mutex, and holding two locks in a defined order is the whole reason
    // put() takes them separately.
    std::vector<std::uint64_t> keys;
    {
        std::lock_guard<std::mutex> lock(index_mutex_);
        const auto it = path_index_.find(canonical_path);
        if (it != path_index_.end()) {
            keys.assign(it->second.begin(), it->second.end());
            path_index_.erase(it);
        }
    }

    std::size_t removed = 0;
    for (const std::uint64_t key : keys) {
        if (cache_.erase(key)) {
            ++removed;
        }
    }
    path_invalidations_.fetch_add(removed, std::memory_order_relaxed);
    return removed;
}

void ToolCache::clear() {
    cache_.clear();
    std::lock_guard<std::mutex> lock(index_mutex_);
    path_index_.clear();
}

GitRevision ToolCache::revision() const {
    return GitProbe::revision(workspace_root_);
}

std::u8string ToolCache::canonical(const fs::path& path) const {
    return GitProbe::canonical_path(path);
}

ToolCacheStats ToolCache::stats() const {
    ToolCacheStats out;
    out.lru = cache_.stats();
    out.key_builds = key_builds_.load(std::memory_order_relaxed);
    out.path_invalidations = path_invalidations_.load(std::memory_order_relaxed);
    out.invalidate_calls = invalidate_calls_.load(std::memory_order_relaxed);
    out.git_repo_found = !gitdir_.empty();
    out.head_sha = gitdir_.empty() ? 0u : GitProbe::head_sha(gitdir_);
    out.index_mtime_ns = gitdir_.empty() ? 0u : GitProbe::index_mtime(gitdir_);
    return out;
}

}  // namespace wx::core::cache