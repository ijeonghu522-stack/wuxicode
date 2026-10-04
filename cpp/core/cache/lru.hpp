// WUXICODE — byte-capacity LRU with LRU eviction policy.
//
// SCOPE: this is policy only. It knows nothing about files, git, tools or
// arguments. It stores opaque byte strings keyed by a 64-bit hash and evicts
// to stay under a byte budget. Everything about *what* a key means lives one
// layer up, in tool_cache.hpp.
//
// WHY A MUTEX AND NOT A LOCK-FREE STRUCTURE:
// ADR 0002 makes parallel READ a headline feature, so this cache is genuinely
// hit from several threads at once. The contended region is a handful of
// pointer comparisons and one std::list splice — on the order of 100 ns —
// while a cache *miss* costs a filesystem read measured in microseconds.
// A lock-free ring here would trade a real, observable serialisation point for
// an abstraction that only pays off once the critical section stops touching
// shared memory. Measured later, not now: the API below is deliberately the
// shape a sharded or lock-free replacement would keep.
//
// WHY BYTES AND NOT ENTRY COUNT:
// Tool results are wildly non-uniform. A `git_status` result is 40 bytes; a
// `read` of a generated header is 2 MB. An entry-count bound would let a
// hundred small entries and one large one sit at the same nominal size, and
// the resident set would track entry count rather than memory. The budget here
// is bytes, so RSS is bounded by the number the caller chose.
//
// NO TTL. Expiry is decided by the key material (see tool_cache.hpp), never by
// the clock. A timer that expires an entry because it is "old" expires it for
// a file that has not changed, which is precisely the recompute this project
// exists to avoid.

#pragma once

#include <cstddef>
#include <cstdint>
#include <list>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace wx::core::cache {

struct LruStats {
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    std::uint64_t inserts = 0;
    std::uint64_t updates = 0;       // put() on a key already resident
    std::uint64_t evictions = 0;     // entries dropped to stay under budget
    std::uint64_t erased = 0;        // explicit erase() calls that hit
    std::uint64_t rejected = 0;      // values too large to ever fit
    std::uint64_t bytes_resident = 0;
    std::size_t entries = 0;
};

// Not copyable: the mutex and the internal list must never be duplicated.
class LruCache {
public:
    static constexpr std::size_t kDefaultCapacityBytes = 160u * 1024u * 1024u;

    explicit LruCache(std::size_t capacity_bytes = kDefaultCapacityBytes)
        : capacity_bytes_(capacity_bytes) {}

    ~LruCache() = default;

    LruCache(const LruCache&) = delete;
    LruCache& operator=(const LruCache&) = delete;
    LruCache(LruCache&&) = delete;
    LruCache& operator=(LruCache&&) = delete;

    // --- lookup ---------------------------------------------------------
    //
    // A hit moves the entry to the most-recently-used end. `out` is only
    // written on a hit, so a caller that passes a reused buffer never sees it
    // clobbered by a miss.
    //
    // Copies under the lock rather than handing out a reference into the list:
    // the entry can be evicted the instant the lock is released, and a caller
    // reading a std::string from a destroyed node is a use-after-free that only
    // shows up under eviction pressure.
    [[nodiscard]] bool get(std::uint64_t key, std::string& out) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto it = index_.find(key);
        if (it == index_.end()) {
            ++stats_.misses;
            return false;
        }
        ++stats_.hits;
        order_.splice(order_.begin(), order_, it->second);
        out = it->second->value;
        return true;
    }

    // Lookup that does NOT promote. For diagnostics and for the read tool's
    // "is this worth caching" check, where moving the recency order would
    // distort the eviction order the cache is actually maintaining.
    [[nodiscard]] bool contains(std::uint64_t key) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return index_.find(key) != index_.end();
    }

    // --- insertion ------------------------------------------------------
    //
    // Returns false when the value cannot be stored at all: it is larger than
    // the entire budget, so keeping it would mean the cache holds nothing else
    // forever. Refusing is the only honest option — admitting it would evict
    // every other entry on every insert and turn the cache into a pass-through
    // that costs a copy plus a mutex on every read.
    //
    // Re-putting a resident key overwrites the value and promotes it, keeping
    // the byte accounting correct in both directions.
    bool put(std::uint64_t key, std::string value) {
        const std::size_t cost = value.size();

        std::lock_guard<std::mutex> lock(mutex_);

        if (cost > capacity_bytes_) {
            // Also drop any older, smaller copy: the caller's intent is clearly
            // "this is the current content", and a stale entry that can never
            // be replaced is worse than no entry.
            erase_locked(key);
            ++stats_.rejected;
            return false;
        }

        const auto existing = index_.find(key);
        if (existing != index_.end()) {
            auto node = existing->second;
            bytes_resident_ -= node->value.size();
            node->value = std::move(value);
            bytes_resident_ += node->value.size();
            order_.splice(order_.begin(), order_, node);
            ++stats_.updates;
            return true;
        }

        order_.emplace_front();
        auto& node = order_.front();
        node.key = key;
        node.value = std::move(value);
        bytes_resident_ += node.value.size();
        index_.emplace(key, order_.begin());
        ++stats_.inserts;

        evict_to_budget_locked();
        return true;
    }

    // --- removal --------------------------------------------------------

    bool erase(std::uint64_t key) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!erase_locked(key)) {
            return false;
        }
        ++stats_.erased;
        return true;
    }

    // Drop every entry whose key satisfies `pred`. Returns how many went.
    //
    // This is how orphaned entries get reclaimed. A cache key includes the
    // target's mtime and size, so editing a file makes the previous entry
    // unreachable — correct, because it must not be served, but it still
    // occupies bytes until ordinary pressure evicts it. Callers that know a
    // specific path just moved (a WRITE finishing) can sweep it immediately
    // instead of waiting for the budget to fill.
    template <typename Predicate>
    std::size_t erase_if(Predicate&& pred) {
        std::lock_guard<std::mutex> lock(mutex_);

        // Collect first, erase after: pred() runs caller code, and mutating
        // order_ underneath an in-flight iteration is the classic way to lose
        // list iterators and corrupt the splice targets.
        std::vector<std::uint64_t> doomed;
        doomed.reserve(64);
        for (const auto& node : order_) {
            if (pred(node.key)) {
                doomed.push_back(node.key);
            }
        }
        for (const std::uint64_t key : doomed) {
            erase_locked(key);
        }
        stats_.erased += doomed.size();
        return doomed.size();
    }

    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        order_.clear();
        index_.clear();
        bytes_resident_ = 0;
    }

    // Lowering the budget evicts immediately; raising it does not prefetch.
    // A budget of 0 disables caching: put() rejects everything, get() always
    // misses, and no memory is held.
    void set_capacity_bytes(std::size_t capacity_bytes) {
        std::lock_guard<std::mutex> lock(mutex_);
        capacity_bytes_ = capacity_bytes;
        evict_to_budget_locked();
    }

    [[nodiscard]] std::size_t capacity_bytes() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return capacity_bytes_;
    }

    [[nodiscard]] LruStats stats() const {
        std::lock_guard<std::mutex> lock(mutex_);
        LruStats out = stats_;
        out.bytes_resident = bytes_resident_;
        out.entries = order_.size();
        return out;
    }

    // Lookup ratio in [0,1]; 1.0 when nothing has been looked up yet, which is
    // the neutral value for a status bar that divides by it.
    [[nodiscard]] double hit_ratio() const {
        std::lock_guard<std::mutex> lock(mutex_);
        const std::uint64_t total = stats_.hits + stats_.misses;
        if (total == 0) {
            return 1.0;
        }
        return static_cast<double>(stats_.hits) / static_cast<double>(total);
    }

private:
    struct Node {
        std::uint64_t key = 0;
        std::string value;
    };

    using Order = std::list<Node>;

    // Caller must hold mutex_.
    bool erase_locked(std::uint64_t key) {
        const auto it = index_.find(key);
        if (it == index_.end()) {
            return false;
        }
        bytes_resident_ -= it->second->value.size();
        order_.erase(it->second);
        index_.erase(it);
        return true;
    }

    // Caller must hold mutex_. Evicts from the cold end until the budget is
    // met. The entry just inserted is at the hot end, so it can only be evicted
    // by this loop if it alone exceeds the budget — which put() already ruled
    // out. One pass is therefore always sufficient; no do/while.
    void evict_to_budget_locked() {
        while (bytes_resident_ > capacity_bytes_ && !order_.empty()) {
            auto& victim = order_.back();
            bytes_resident_ -= victim.value.size();
            index_.erase(victim.key);
            order_.pop_back();
            ++stats_.evictions;
        }
    }

    // front() = most recently used, back() = eviction candidate.
    Order order_;
    std::unordered_map<std::uint64_t, Order::iterator> index_;

    mutable std::mutex mutex_;
    std::size_t capacity_bytes_ = 0;
    std::size_t bytes_resident_ = 0;
    LruStats stats_{};
};

}  // namespace wx::core::cache