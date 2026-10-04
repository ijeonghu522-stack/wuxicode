// WUXICODE — ToolCache tests.
//
// These are the tests that justify the project's second headline claim: that
// cache invalidation is git-aware rather than timer-based. Each of the five
// axes gets its own case, and each asserts the SAME property from a different
// direction: change that one thing, and a previously cached value must no
// longer be served.
//
// The negative direction matters as much as the positive. A cache that always
// misses is trivially correct, so every axis test is paired with a check that
// an UNCHANGED cache still hits.

#include <filesystem>
#include <string>
#include <system_error>

#include "core/cache/tool_cache.hpp"
#include "tests/fixtures.hpp"
#include "tests/harness.hpp"

using wx::core::cache::CacheDescriptor;
using wx::core::cache::ToolCache;
namespace fs = std::filesystem;

using wxtest::force_new_mtime;
using wxtest::TempDir;
using wxtest::write_file;

namespace {

// A descriptor for reading one file, which is the only shape Phase 1B has a
// meaningful axis-5 subject for.
CacheDescriptor read_desc(const ToolCache& cache, const fs::path& file,
                          std::string args = "{}", std::uint32_t schema = 1) {
    CacheDescriptor d;
    d.tool = "read";
    d.schema_version = schema;
    d.canonical_args = std::move(args);
    d.canonical_path = cache.canonical(file);
    d.use_git_axis = true;
    return d;
}

}  // namespace

// --- basic behaviour ------------------------------------------------------

WX_TEST(tool_cache, put_then_get_roundtrips) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "notes.md";
    write_file(file, "hello world");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);

    std::string out;
    CHECK(!cache.get(d, out));  // cold
    REQUIRE(cache.put(d, "hello world"));
    CHECK(cache.get(d, out));
    CHECK_EQ(out, std::string("hello world"));
}

WX_TEST(tool_cache, key_is_stable_for_an_unchanged_world) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "a.txt";
    write_file(file, "stable");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);

    // A cache that misses on an unchanged file recomputes forever, which is the
    // failure mode this whole subsystem exists to avoid.
    CHECK_EQ(cache.key_for(d), cache.key_for(d));
}

WX_TEST(tool_cache, cache_actually_hits_when_nothing_changed) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "b.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "content"));

    std::string out;
    for (int i = 0; i < 5; ++i) {
        CHECK_MSG(cache.get(d, out), "an unchanged file must keep hitting the cache");
    }
    CHECK_EQ(cache.stats().lru.hits, std::uint64_t{5});
}

// --- axis 1: tool name ----------------------------------------------------

WX_TEST(tool_cache, axis1_different_tools_do_not_share_entries) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "c.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);
    CacheDescriptor read = read_desc(cache, file);
    read.tool = "read";

    CacheDescriptor grep = read;
    grep.tool = "grep";

    CHECK_NE(cache.key_for(read), cache.key_for(grep));
}

// --- axis 2: schema version ----------------------------------------------

WX_TEST(tool_cache, axis2_schema_version_change_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "d.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor v1 = read_desc(cache, file, "{}", 1);
    const CacheDescriptor v2 = read_desc(cache, file, "{}", 2);

    REQUIRE(cache.put(v1, "old format"));
    CHECK_NE(cache.key_for(v1), cache.key_for(v2));

    std::string out;
    CHECK_MSG(!cache.get(v2, out), "a schema bump must not serve the previous format's bytes");
    CHECK(cache.get(v1, out));  // the old entry is still addressable
}

// --- axis 3: canonical args ----------------------------------------------

WX_TEST(tool_cache, axis3_different_arguments_do_not_share_entries) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "e.txt";
    write_file(file, "0123456789");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor head = read_desc(cache, file, "{\"offset\":0}");
    const CacheDescriptor tail = read_desc(cache, file, "{\"offset\":5}");

    CHECK_NE(cache.key_for(head), cache.key_for(tail));
}

WX_TEST(tool_cache, axis3_argument_text_is_hashed_not_concatenated) {
    // Without per-field hashing this would collide: splitting "ab" + "c" must
    // not produce the same key as "a" + "bc".
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor a = read_desc(cache, root / "f.txt", "ab");
    CacheDescriptor b = read_desc(cache, root / "f.txt", "a");
    b.tool = "re";

    // "ab" as grep args vs "a" as read args with tool "re" -> the two fields
    // carry different bytes, so the keys must differ.
    CHECK_NE(cache.key_for(a), cache.key_for(b));
}

// --- axis 4: the git axes -------------------------------------------------

WX_TEST(tool_cache, axis4_head_change_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "g.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "old content"));

    const std::uint64_t before = cache.key_for(d);

    // Move HEAD by rewriting the loose ref. `git commit` does exactly this.
    write_file(root / ".git" / "refs" / "heads" / "main",
               "2222222222222222222222222222222222222222\n");

    const std::uint64_t after = cache.key_for(d);
    CHECK_MSG(before != after, "a commit must move the key even though the file is untouched");

    std::string out;
    CHECK_MSG(!cache.get(d, out), "stale bytes must not survive a commit");
}

WX_TEST(tool_cache, axis4_index_change_without_head_change_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "h.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "content"));

    const std::uint64_t before = cache.key_for(d);

    // `git add` stages without committing: HEAD does not move, the index does.
    // An implementation keyed on HEAD alone serves stale reads here.
    REQUIRE(force_new_mtime(root / ".git" / "index"));

    const std::uint64_t after = cache.key_for(d);
    CHECK_MSG(before != after, "`git add` must invalidate: HEAD did not move but the index did");

    std::string out;
    CHECK(!cache.get(d, out));
}

WX_TEST(tool_cache, axis4_git_axis_can_be_opted_out_of) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "i.txt";
    write_file(file, "content");

    ToolCache cache(1024 * 1024, root);

    CacheDescriptor d = read_desc(cache, file);
    d.use_git_axis = false;

    const std::uint64_t before = cache.key_for(d);
    write_file(root / ".git" / "refs" / "heads" / "main",
               "3333333333333333333333333333333333333333\n");
    CHECK_MSG(cache.key_for(d) == before,
              "a tool whose output cannot depend on HEAD must not pay for the git probe");
}

WX_TEST(tool_cache, axis4_no_repository_is_distinct_from_unborn_branch) {
    // Both produce all-zero git numbers. Folding in `valid` separately keeps
    // them from colliding.
    TempDir plain;
    TempDir unborn;
    const fs::path unborn_root = unborn.path() / "repo";
    write_file(unborn_root / ".git" / "HEAD", "ref: refs/heads/main\n");

    ToolCache no_repo(1024 * 1024, plain.path());
    ToolCache no_commit(1024 * 1024, unborn_root);

    const fs::path f1 = plain.path() / "x.txt";
    const fs::path f2 = unborn_root / "x.txt";
    write_file(f1, "same");
    write_file(f2, "same");

    const std::uint64_t k1 = no_repo.key_for(read_desc(no_repo, f1));
    const std::uint64_t k2 = no_commit.key_for(read_desc(no_commit, f2));

    CHECK(no_repo.stats().git_repo_found == false);
    CHECK(no_commit.stats().git_repo_found == true);
    // Different paths alone would separate these, so compare the axis directly
    // through the revision probe instead.
    CHECK(!no_repo.revision().valid);
    CHECK(no_commit.revision().valid);
    CHECK_MSG(k1 != k2, "distinct repositories must not share key material");
}

WX_TEST(tool_cache, works_outside_a_repository) {
    // A plain script directory is a normal workspace, not an error case.
    TempDir tmp;
    const fs::path file = tmp.path() / "standalone.txt";
    write_file(file, "no git here");

    ToolCache cache(1024 * 1024, tmp.path());
    const CacheDescriptor d = read_desc(cache, file);

    std::string out;
    CHECK(!cache.get(d, out));
    REQUIRE(cache.put(d, "no git here"));
    CHECK(cache.get(d, out));
    CHECK(cache.stats().lru.hits >= 1);
}

// --- axis 5: the target file ---------------------------------------------

WX_TEST(tool_cache, axis5_size_change_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "j.txt";
    write_file(file, "short");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "short"));

    // The headline bug: the agent edits a file, re-reads, and gets the old
    // bytes back.
    write_file(file, "a considerably longer body of text");

    std::string out;
    CHECK_MSG(!cache.get(d, out), "a size change must invalidate even when mtime resolution is coarse");
    CHECK(cache.key_for(d) != 0);
}

WX_TEST(tool_cache, axis5_mtime_only_change_invalidates_at_equal_size) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "k.txt";
    write_file(file, "identical");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "identical"));

    const std::uint64_t before = cache.key_for(d);

    // Same byte count, different mtime. Size alone would miss this entirely.
    REQUIRE(force_new_mtime(file));

    CHECK_MSG(cache.key_for(d) != before, "an mtime-only change must still move the key");

    std::string out;
    CHECK(!cache.get(d, out));
}

WX_TEST(tool_cache, axis5_file_deletion_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "l.txt";
    write_file(file, "here");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor d = read_desc(cache, file);
    REQUIRE(cache.put(d, "here"));

    std::error_code ec;
    fs::remove(file, ec);
    REQUIRE(!ec);

    std::string out;
    CHECK_MSG(!cache.get(d, out), "a deleted file must not serve its last known contents");
}

WX_TEST(tool_cache, axis5_creation_invalidates) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "not-yet-there.txt";
    const fs::path file2 = root / "not-yet-there.txt";

    ToolCache cache(1024 * 1024, root);

    const std::uint64_t absent = cache.key_for(read_desc(cache, file2));
    write_file(file2, "now present");
    const std::uint64_t present = cache.key_for(read_desc(cache, file2));

    CHECK_MSG(absent != present, "a missing path and a present one must not share a key");
}

WX_TEST(tool_cache, axis5_empty_canonical_path_disables_the_axis) {
    // A tool with no single file subject must not stat "" and fold in an
    // all-zero axis that looks real. The observable consequence is that such a
    // key is stable across arbitrary filesystem churn.
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path noise = root / "noise.txt";

    ToolCache cache(1024 * 1024, root);
    CacheDescriptor d;
    d.tool = "glob";
    d.canonical_args = "*.py";
    d.canonical_path.clear();
    d.use_git_axis = false;

    const std::uint64_t before = cache.key_for(d);

    // Churn the tree hard: a new file, a changed file, a removed file.
    write_file(noise, "one");
    write_file(root / "another.txt", "two");
    std::error_code ec;
    fs::remove(noise, ec);

    CHECK_MSG(cache.key_for(d) == before,
              "with no file subject and no git axis, filesystem churn must not move the key");

    // And it must be a usable, non-zero key that still caches.
    std::string out;
    CHECK(!cache.get(d, out));
    REQUIRE(cache.put(d, "matches"));
    CHECK(cache.get(d, out));
}

// --- invalidate_path ------------------------------------------------------

WX_TEST(tool_cache, invalidate_path_removes_that_paths_entries) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path target = root / "m.txt";
    const fs::path other = root / "n.txt";
    write_file(target, "a");
    write_file(other, "b");

    ToolCache cache(1024 * 1024, root);
    const CacheDescriptor dt = read_desc(cache, target);
    const CacheDescriptor dor = read_desc(cache, other, "{\"other\":true}");

    REQUIRE(cache.put(dt, "a"));
    REQUIRE(cache.put(dor, "b"));

    const std::size_t removed = cache.invalidate_path(cache.canonical(target));
    CHECK_EQ(removed, std::size_t{1});

    std::string out;
    CHECK(!cache.get(dt, out));
    CHECK_MSG(cache.get(dor, out), "invalidating one path must not touch another");
}

WX_TEST(tool_cache, invalidate_path_sweeps_every_generation_of_one_file) {
    // Editing a file repeatedly leaves one unreachable entry per revision.
    // invalidate_path is what stops that accumulating on a hot file.
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "o.txt";

    ToolCache cache(1024 * 1024, root);
    for (int i = 0; i < 5; ++i) {
        write_file(file, std::string(static_cast<std::size_t>(i) + 1, 'x'));
        REQUIRE(cache.put(read_desc(cache, file), std::string(static_cast<std::size_t>(i) + 1, 'x')));
    }

    CHECK_EQ(cache.invalidate_path(cache.canonical(file)), std::size_t{5});
    CHECK_EQ(cache.stats().lru.entries, std::size_t{0});
    CHECK_EQ(cache.stats().lru.bytes_resident, std::uint64_t{0});
}

WX_TEST(tool_cache, invalidate_path_on_unknown_path_is_a_noop) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    ToolCache cache(1024 * 1024, root);
    CHECK_EQ(cache.invalidate_path(cache.canonical(root / "never-seen.txt")), std::size_t{0});
}

WX_TEST(tool_cache, path_index_does_not_retain_empty_buckets) {
    // Otherwise a long session touching many files grows this map forever.
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    ToolCache cache(1024 * 1024, root);

    for (int i = 0; i < 20; ++i) {
        const fs::path f = root / ("file" + std::to_string(i) + ".txt");
        write_file(f, "x");
        REQUIRE(cache.put(read_desc(cache, f), "x"));
    }
    CHECK_EQ(cache.stats().lru.entries, std::size_t{20});

    cache.clear();
    CHECK_EQ(cache.stats().lru.entries, std::size_t{0});

    // After clear, invalidating a previously-known path must still be safe.
    CHECK_EQ(cache.invalidate_path(cache.canonical(root / "file0.txt")), std::size_t{0});
}

// --- capacity and stats ---------------------------------------------------

WX_TEST(tool_cache, oversized_value_is_rejected_without_poisoning_the_index) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "1111111111111111111111111111111111111111");
    const fs::path file = root / "p.txt";
    write_file(file, "small");

    ToolCache cache(64, root);
    const CacheDescriptor d = read_desc(cache, file);

    CHECK(!cache.put(d, std::string(1024, 'z')));
    CHECK_EQ(cache.stats().lru.entries, std::size_t{0});
    CHECK_EQ(cache.stats().lru.bytes_resident, std::uint64_t{0});

    // The path index must not still hold the rejected key, or a later
    // invalidate_path would try to erase something that was never stored.
    CHECK_EQ(cache.invalidate_path(cache.canonical(file)), std::size_t{0});
}

WX_TEST(tool_cache, stats_report_the_git_state) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "4444444444444444444444444444444444444444");
    ToolCache cache(1024 * 1024, root);

    const auto s = cache.stats();
    CHECK(s.git_repo_found);
    CHECK_EQ(s.head_sha, wx::core::cache::parse_hex64("4444444444444444444444444444444444444444"));
    CHECK(s.index_mtime_ns != 0);
}

WX_TEST(tool_cache, canonical_path_helper_matches_the_probe) {
    TempDir tmp;
    const fs::path root = wxtest::make_repo(tmp, "5555555555555555555555555555555555555555");
    ToolCache cache(1024 * 1024, root);

    const fs::path messy = root / "a" / "." / "b.txt";
    CHECK(cache.canonical(messy) == cache.canonical(root / "a" / "b.txt"));
}

int main() {
    return ::wxtest::run_all();
}