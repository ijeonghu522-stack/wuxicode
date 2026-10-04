// WUXICODE — LRU cache tests.
//
// Focus is the byte accounting and the eviction ORDER, because those are the
// two things that go wrong quietly: a byte leak that only shows up after an
// hour of runtime, and an order bug that only shows up once the cache is under
// pressure.

#include <string>

#include "core/cache/lru.hpp"
#include "tests/harness.hpp"

using wx::core::cache::LruCache;

namespace {

std::string sized(std::size_t n, char c = 'x') {
    return std::string(n, c);
}

}  // namespace

WX_TEST(lru, empty_cache_misses) {
    LruCache c(1024);
    std::string out = "untouched";
    CHECK(!c.get(1, out));
    // A miss must not write to `out`: callers pass a reused buffer, and
    // clobbering it on a miss would hand back the previous key's contents.
    CHECK_EQ(out, std::string("untouched"));
    CHECK_EQ(c.stats().misses, std::uint64_t{1});
    CHECK_EQ(c.stats().hits, std::uint64_t{0});
}

WX_TEST(lru, put_then_get_roundtrips) {
    LruCache c(1024);
    REQUIRE(c.put(42, "hello"));
    std::string out;
    CHECK(c.get(42, out));
    CHECK_EQ(out, std::string("hello"));
    CHECK_EQ(c.stats().hits, std::uint64_t{1});
    CHECK_EQ(c.stats().inserts, std::uint64_t{1});
    CHECK_EQ(c.stats().entries, std::size_t{1});
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{5});
}

WX_TEST(lru, evicts_least_recently_used_when_over_budget) {
    LruCache c(10);
    REQUIRE(c.put(1, sized(4)));  // 4 bytes, total 4
    REQUIRE(c.put(2, sized(4)));  // total 8
    REQUIRE(c.put(3, sized(4)));  // total 12 -> over budget, evict #1

    std::string out;
    CHECK(!c.get(1, out));  // evicted: oldest
    CHECK(c.get(2, out));
    CHECK(c.get(3, out));
    CHECK_EQ(c.stats().evictions, std::uint64_t{1});
    CHECK(c.stats().bytes_resident <= c.capacity_bytes());
}

WX_TEST(lru, get_promotes_and_changes_eviction_order) {
    LruCache c(10);
    REQUIRE(c.put(1, sized(4)));
    REQUIRE(c.put(2, sized(4)));  // order: 2 (hot), 1 (cold)

    // Touch 1 so that 2 becomes the eviction candidate.
    std::string out;
    REQUIRE(c.get(1, out));

    REQUIRE(c.put(3, sized(4)));  // must evict 2, not 1
    CHECK(c.get(1, out));
    CHECK_MSG(!c.get(2, out), "expected key 2 evicted: it was the least recently used");
    CHECK(c.get(3, out));
}

WX_TEST(lru, contains_does_not_promote) {
    LruCache c(10);
    REQUIRE(c.put(1, sized(4)));  // order: [1] cold
    REQUIRE(c.put(2, sized(4)));  // order: [2] hot, [1] cold

    // contains() is a diagnostic, not an access. If it promoted, 1 would become
    // hot and 2 would become the eviction candidate.
    CHECK(c.contains(1));
    CHECK(c.contains(2));

    REQUIRE(c.put(3, sized(4)));  // evicts the cold entry

    // Order was untouched by contains(), so 1 is still the coldest and goes.
    CHECK_MSG(!c.contains(1), "contains() must not promote: key 1 was still the coldest entry");
    CHECK_MSG(c.contains(2), "key 2 was the most recently used and must survive");
    CHECK(c.contains(3));
}

WX_TEST(lru, oversized_value_is_rejected_and_drops_stale_copy) {
    LruCache c(1000);
    REQUIRE(c.put(1, sized(4)));

    // Larger than the entire budget. Storing it would evict every other entry
    // and then hold nothing useful, so the cache refuses. The refusal must also
    // drop the older, smaller copy of the SAME key: the caller's intent is
    // plainly "this is the current content", and a stale entry that can never
    // be replaced is worse than no entry at all.
    CHECK(!c.put(1, sized(100000)));
    CHECK_EQ(c.stats().rejected, std::uint64_t{1});

    std::string out;
    CHECK_MSG(!c.get(1, out), "a rejected put must clear the previous copy of that key");
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{0});
}

WX_TEST(lru, oversized_value_for_another_key_leaves_resident_entries_alone) {
    // Rejection is about the value, not a licence to flush the cache. An entry
    // that fits comfortably must survive another key's rejected put.
    LruCache c(1000);
    REQUIRE(c.put(1, sized(4)));
    CHECK(!c.put(2, sized(100000)));

    std::string out;
    CHECK_MSG(c.get(1, out), "a rejected put for key 2 must not evict key 1");
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{4});
}

WX_TEST(lru, update_existing_key_keeps_bytes_correct) {
    LruCache c(1000);
    REQUIRE(c.put(1, sized(10)));
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{10});

    // Shrink: bytes_resident must go DOWN by the difference.
    REQUIRE(c.put(1, sized(4)));
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{4});
    CHECK_EQ(c.stats().entries, std::size_t{1});
    CHECK_EQ(c.stats().updates, std::uint64_t{1});

    // Grow: and UP again, with no eviction side effects at this budget.
    REQUIRE(c.put(1, sized(50)));
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{50});
    CHECK_EQ(c.stats().entries, std::size_t{1});
    CHECK_EQ(c.stats().evictions, std::uint64_t{0});
}

WX_TEST(lru, shrinking_capacity_evicts_immediately) {
    LruCache c(1000);
    REQUIRE(c.put(1, sized(10)));
    REQUIRE(c.put(2, sized(10)));
    REQUIRE(c.put(3, sized(10)));

    c.set_capacity_bytes(10);
    std::string out;
    CHECK(c.get(3, out));  // most recent survives
    CHECK(!c.get(1, out));
    CHECK(!c.get(2, out));
    CHECK(c.stats().bytes_resident <= std::uint64_t{10});
}

WX_TEST(lru, zero_capacity_disables_caching) {
    LruCache c(0);
    CHECK(!c.put(1, "anything"));
    std::string out;
    CHECK(!c.get(1, out));
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{0});
}

WX_TEST(lru, erase_removes_entry_and_frees_bytes) {
    LruCache c(1000);
    REQUIRE(c.put(1, sized(10)));
    CHECK(c.erase(1));
    CHECK(!c.erase(1));  // second erase is a no-op, not a crash
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{0});
    CHECK_EQ(c.stats().entries, std::size_t{0});
}

WX_TEST(lru, erase_if_removes_matching_keys_only) {
    LruCache c(1000);
    REQUIRE(c.put(10, "a"));
    REQUIRE(c.put(20, "b"));
    REQUIRE(c.put(30, "c"));

    const std::size_t removed = c.erase_if([](std::uint64_t key) { return key != 20; });
    CHECK_EQ(removed, std::size_t{2});
    CHECK(c.contains(20));
    CHECK(!c.contains(10));
    CHECK(!c.contains(30));
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{1});
}

WX_TEST(lru, clear_resets_everything) {
    LruCache c(1000);
    REQUIRE(c.put(1, sized(10)));
    c.clear();
    CHECK_EQ(c.stats().entries, std::size_t{0});
    CHECK_EQ(c.stats().bytes_resident, std::uint64_t{0});
    std::string out;
    CHECK(!c.get(1, out));
}

WX_TEST(lru, hit_ratio_is_neutral_before_any_lookup) {
    LruCache c(100);
    // A status bar dividing by this must not render NaN.
    CHECK_EQ(c.hit_ratio(), 1.0);

    std::string out;
    CHECK(!c.get(1, out));  // miss
    REQUIRE(c.put(1, "v"));
    CHECK(c.get(1, out));   // hit
    CHECK_EQ(c.hit_ratio(), 0.5);
}

WX_TEST(lru, evicting_to_exact_budget_keeps_everything_it_can) {
    LruCache c(12);
    REQUIRE(c.put(1, sized(4)));
    REQUIRE(c.put(2, sized(4)));
    REQUIRE(c.put(3, sized(4)));  // exactly 12 == budget, nothing evicted
    CHECK_EQ(c.stats().evictions, std::uint64_t{0});
    CHECK_EQ(c.stats().entries, std::size_t{3});

    REQUIRE(c.put(4, sized(4)));  // 16 -> evict exactly one
    CHECK_EQ(c.stats().evictions, std::uint64_t{1});
    CHECK_EQ(c.stats().entries, std::size_t{3});
}

int main() {
    return ::wxtest::run_all();
}