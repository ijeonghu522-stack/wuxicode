// WUXICODE — GitProbe tests.
//
// FIXTURES, NOT A REAL REPOSITORY. Every git-shaped directory here is built by
// writing plain files. That makes the tests deterministic, offline, and free of
// a `git` binary — but it also means they exercise what the probe READS, which
// is the actual contract. If the probe starts shelling out to git instead, these
// tests should keep passing while the production path changes underneath them,
// and that is worth knowing.
//
// Every fixture lives under the system temp directory, never inside the working
// tree. A fixture placed inside this repository would have repo_root() walk up
// and find the REAL .git, and the "no repository here" cases would silently
// pass for the wrong reason.

#include <chrono>
#include <filesystem>
#include <string>
#include <system_error>

#include "core/cache/git_probe.hpp"
#include "tests/fixtures.hpp"
#include "tests/harness.hpp"

using wx::core::cache::FileStamp;
using wx::core::cache::GitProbe;
using wx::core::cache::GitRevision;
namespace fs = std::filesystem;

// The fixtures live in wxtest; pull the two used most into this file's scope.
using wxtest::TempDir;
using wxtest::write_file;

namespace {

// A minimal but genuinely shaped repository: real HEAD, a real loose ref, a
// real index file.
fs::path make_repo_with_loose_ref(const TempDir& tmp, const std::string& sha) {
    return wxtest::make_repo(tmp, sha, "loose");
}

}  // namespace

// --- repo_root ------------------------------------------------------------

WX_TEST(git_probe, finds_repo_root_from_nested_subdirectory) {
    TempDir tmp;
    const fs::path root = make_repo_with_loose_ref(tmp, "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678");

    const fs::path deep = root / "a" / "b" / "c";
    fs::create_directories(deep);

    CHECK_EQ(GitProbe::repo_root(deep).lexically_normal(), root.lexically_normal());
    CHECK_EQ(GitProbe::repo_root(root).lexically_normal(), root.lexically_normal());
}

WX_TEST(git_probe, no_repository_upward_returns_empty) {
    TempDir tmp;
    // The fixture root itself has no .git, and neither does anything above it
    // in the temp tree.
    CHECK(GitProbe::repo_root(tmp.path()).empty());
}

WX_TEST(git_probe, repo_root_does_not_require_the_start_path_to_exist) {
    TempDir tmp;
    const fs::path root = make_repo_with_loose_ref(tmp, "1111111111111111111111111111111111111111");
    // A path for a file that was just deleted. Probing it must not throw.
    const fs::path gone = root / "deleted" / "never" / "was.txt";
    CHECK_EQ(GitProbe::repo_root(gone).lexically_normal(), root.lexically_normal());
}

// --- resolve_gitdir -------------------------------------------------------

WX_TEST(git_probe, resolves_plain_dot_git_directory) {
    TempDir tmp;
    const fs::path root = make_repo_with_loose_ref(tmp, "2222222222222222222222222222222222222222");
    const fs::path gitdir = GitProbe::resolve_gitdir(root);
    CHECK_EQ(gitdir, (root / ".git").lexically_normal());
}

WX_TEST(git_probe, resolves_worktree_gitdir_file) {
    TempDir tmp;
    // A linked worktree has a .git FILE: `gitdir: <path to real gitdir>`.
    // Treating it as a directory reports "no repository" for every worktree,
    // which is where agents do their concurrent work.
    const fs::path real_gitdir = tmp.path() / "main" / ".git" / "worktrees" / "wt1";
    write_file(real_gitdir / "HEAD", "ref: refs/heads/feature\n");

    const fs::path worktree = tmp.path() / "wt1";
    write_file(worktree / ".git", "gitdir: " + real_gitdir.generic_string() + "\n");

    const fs::path resolved = GitProbe::resolve_gitdir(worktree);
    CHECK_EQ(resolved.lexically_normal(), real_gitdir.lexically_normal());
    CHECK(!resolved.empty());
}

WX_TEST(git_probe, worktree_revision_is_probed_through_the_gitdir_file) {
    TempDir tmp;
    const fs::path real_gitdir = tmp.path() / "main" / ".git" / "worktrees" / "wt2";
    write_file(real_gitdir / "HEAD", "ref: refs/heads/feature\n");
    write_file(real_gitdir / "refs" / "heads" / "feature", "abcdef0123456789abcdef0123456789abcdef01\n");

    const fs::path worktree = tmp.path() / "wt2";
    write_file(worktree / ".git", "gitdir: " + real_gitdir.generic_string() + "\n");

    const GitRevision rev = GitProbe::revision(worktree);
    CHECK(rev.valid);
    CHECK_EQ(rev.head_sha, wx::core::cache::parse_hex64("abcdef0123456789abcdef0123456789abcdef01"));
}

WX_TEST(git_probe, unparseable_gitdir_file_is_not_a_repository) {
    TempDir tmp;
    const fs::path root = tmp.path() / "broken";
    write_file(root / ".git", "this is not a gitdir pointer\n");
    fs::create_directories(root);
    CHECK(GitProbe::resolve_gitdir(root).empty());
}

// --- head_sha -------------------------------------------------------------

WX_TEST(git_probe, head_sha_from_loose_ref) {
    TempDir tmp;
    const fs::path root = make_repo_with_loose_ref(tmp, "0123456789abcdef0123456789abcdef01234567");
    const fs::path gitdir = GitProbe::resolve_gitdir(root);
    CHECK_EQ(GitProbe::head_sha(gitdir), wx::core::cache::parse_hex64("0123456789abcdef0123456789abcdef01234567"));
}

WX_TEST(git_probe, head_sha_from_packed_refs_when_loose_ref_is_absent) {
    TempDir tmp;
    // This is the state of every ordinary clone after `git gc`, not an edge
    // case: refs live in packed-refs and there is no loose file at all.
    const fs::path root = tmp.path() / "packed";
    write_file(root / ".git" / "HEAD", "ref: refs/heads/main\n");
    write_file(root / ".git" / "packed-refs",
               "# pack-refs with: peeled fully-peeled sorted \n"
               "0123456789abcdef0123456789abcdef01234567 refs/heads/feature\n"
               "fedcba9876543210fedcba9876543210fedcba98 refs/heads/main\n");
    fs::create_directories(root / "src");
    std::error_code ec;
    fs::create_directories(root, ec);

    const fs::path gitdir = GitProbe::resolve_gitdir(root);
    CHECK_EQ(GitProbe::head_sha(gitdir),
             wx::core::cache::parse_hex64("fedcba9876543210fedcba9876543210fedcba98"));
}

WX_TEST(git_probe, packed_refs_peeled_lines_are_not_matched_as_entries) {
    TempDir tmp;
    // A `^sha` line annotates the entry ABOVE it. Treating it as an entry of
    // its own would let a tag's peeled sha be reported as an unrelated ref.
    const fs::path root = tmp.path() / "peeled";
    write_file(root / ".git" / "HEAD", "ref: refs/heads/v2.0\n");
    write_file(root / ".git" / "packed-refs",
               "# pack-refs with: peeled fully-peeled sorted \n"
               "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/v1.0\n"
               "^bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
               "cccccccccccccccccccccccccccccccccccccccc refs/heads/v2.0\n");
    std::error_code ec;
    fs::create_directories(root, ec);

    const fs::path gitdir = GitProbe::resolve_gitdir(root);
    CHECK_EQ(GitProbe::head_sha(gitdir),
             wx::core::cache::parse_hex64("cccccccccccccccccccccccccccccccccccccccc"));
}

WX_TEST(git_probe, head_sha_detached) {
    TempDir tmp;
    const fs::path root = tmp.path() / "detached";
    write_file(root / ".git" / "HEAD", "0123456789abcdef0123456789abcdef01234567\n");
    std::error_code ec;
    fs::create_directories(root, ec);

    CHECK_EQ(GitProbe::head_sha(GitProbe::resolve_gitdir(root)),
             wx::core::cache::parse_hex64("0123456789abcdef0123456789abcdef01234567"));
}

WX_TEST(git_probe, head_sha_unborn_branch_is_zero_not_an_error) {
    TempDir tmp;
    // `git init` with no commit: HEAD names a ref that does not exist yet.
    const fs::path root = tmp.path() / "unborn";
    write_file(root / ".git" / "HEAD", "ref: refs/heads/main\n");
    std::error_code ec;
    fs::create_directories(root, ec);

    const GitRevision rev = GitProbe::revision(root);
    CHECK(rev.valid);      // it IS a repository
    CHECK_EQ(rev.head_sha, std::uint64_t{0});  // just no commit yet
    CHECK_EQ(rev.index_mtime_ns, std::uint64_t{0});
}

WX_TEST(git_probe, missing_head_is_zero) {
    TempDir tmp;
    const fs::path gitdir = tmp.path() / "nohead" / ".git";
    fs::create_directories(gitdir);
    CHECK_EQ(GitProbe::head_sha(gitdir), std::uint64_t{0});
    CHECK_EQ(GitProbe::head_sha(fs::path{}), std::uint64_t{0});
}

// --- index mtime ----------------------------------------------------------

WX_TEST(git_probe, index_mtime_is_nonzero_and_changes_when_the_index_changes) {
    TempDir tmp;
    const fs::path root = make_repo_with_loose_ref(tmp, "9999999999999999999999999999999999999999");
    const fs::path gitdir = GitProbe::resolve_gitdir(root);

    const std::uint64_t first = GitProbe::index_mtime(gitdir);
    CHECK_MSG(first != 0, "a real index file must report a nonzero mtime");

    // Force the timestamp rather than sleeping: a sleep-based test is either
    // slow or flaky, and the point is that the AXIS MOVES, not that time passes.
    std::error_code ec;
    const auto bumped = fs::last_write_time(gitdir / "index") + std::chrono::seconds(30);
    fs::last_write_time(gitdir / "index", bumped, ec);
    REQUIRE(!ec);

    const std::uint64_t second = GitProbe::index_mtime(gitdir);
    CHECK_MSG(second != first, "staging a change must move the index axis without moving HEAD");
}

// --- revision -------------------------------------------------------------

WX_TEST(git_probe, revision_outside_a_repository_is_invalid) {
    TempDir tmp;
    const GitRevision rev = GitProbe::revision(tmp.path());
    CHECK(!rev.valid);
    CHECK_EQ(rev.head_sha, std::uint64_t{0});
    CHECK_EQ(rev.index_mtime_ns, std::uint64_t{0});
}

WX_TEST(git_probe, revision_equality_compares_both_axes) {
    GitRevision a;
    a.valid = true;
    a.head_sha = 111;
    a.index_mtime_ns = 222;

    GitRevision b = a;
    CHECK(a == b);

    b.index_mtime_ns = 223;  // HEAD unchanged, index moved (git add)
    CHECK_MSG(a != b, "the index axis must participate: staging does not move HEAD");
}

// --- stamp ----------------------------------------------------------------

WX_TEST(git_probe, stamp_reports_size_and_existence) {
    TempDir tmp;
    const fs::path file = tmp.path() / "data.bin";
    write_file(file, "0123456789");

    const FileStamp st = GitProbe::stamp(file);
    CHECK(st.exists);
    CHECK(!st.is_directory);
    CHECK_EQ(st.size, std::uint64_t{10});
    CHECK(st.mtime_ns != 0);
}

WX_TEST(git_probe, stamp_of_missing_path_is_not_an_error) {
    TempDir tmp;
    const FileStamp st = GitProbe::stamp(tmp.path() / "nope.txt");
    CHECK(!st.exists);
    CHECK_EQ(st.size, std::uint64_t{0});
    CHECK_EQ(st.mtime_ns, std::uint64_t{0});
}

WX_TEST(git_probe, stamp_of_directory_has_no_size) {
    TempDir tmp;
    const fs::path sub = tmp.path() / "subdir";
    fs::create_directories(sub);

    const FileStamp st = GitProbe::stamp(sub);
    CHECK(st.exists);
    CHECK(st.is_directory);
    // file_size() on a directory is undefined behaviour on Windows; the stamp
    // must leave it at 0 rather than reporting a garbage size.
    CHECK_EQ(st.size, std::uint64_t{0});
}

// --- canonical_path -------------------------------------------------------

WX_TEST(git_probe, canonical_path_is_absolute_and_normalised) {
    std::error_code ec;
    const fs::path cwd = fs::current_path(ec);
    REQUIRE(!ec);

    const fs::path messy = cwd / "a" / "." / "b" / ".." / "b" / "file.txt";
    const std::u8string canon = GitProbe::canonical_path(messy);
    const std::string s(reinterpret_cast<const char*>(canon.data()), canon.size());

    CHECK_MSG(s.find("/./") == std::string::npos, "lexical normalisation must collapse '.': " + s);
    CHECK_MSG(s.find("/../") == std::string::npos, "lexical normalisation must collapse '..': " + s);

    // The tail must survive normalisation intact. Compared as a SUFFIX: on
    // Windows the leading portion is case-folded against the real on-disk
    // casing, so asserting on the whole string would be asserting on the
    // machine, not on the code.
    const std::string tail = "a/b/file.txt";
    CHECK_MSG(s.size() > tail.size() && s.compare(s.size() - tail.size(), tail.size(), tail) == 0,
              "expected the normalised tail, got: " + s);
    CHECK_MSG(s.find(':') != std::string::npos || s[0] == '/', "must be absolute: " + s);
}

WX_TEST(git_probe, canonical_path_uses_forward_slashes) {
    const fs::path mixed = fs::path("sub") / "dir" / "file.txt";
    const std::u8string canon = GitProbe::canonical_path(mixed);
    const std::string s(reinterpret_cast<const char*>(canon.data()), canon.size());
    CHECK_MSG(s.find('\\') == std::string::npos, "canonical form must be separator-normalised");
}

WX_TEST(git_probe, canonical_path_strips_trailing_separator_but_keeps_roots) {
    const std::u8string with_slash = GitProbe::canonical_path(fs::path("some") / "dir" / "");
    const std::u8string without = GitProbe::canonical_path(fs::path("some") / "dir");
    CHECK_EQ(std::string(with_slash.begin(), with_slash.end()),
             std::string(without.begin(), without.end()));

    // A filesystem root must survive intact — collapsing it to "" would make
    // every path under it unmatchable.
    const std::u8string root = GitProbe::canonical_path(fs::path("/"));
    CHECK(root.size() >= 1);
}

#ifdef _WIN32
WX_TEST(git_probe, canonical_path_case_folds_on_windows) {
    // NTFS is case-insensitive, so these name ONE file and must produce ONE key.
    const std::u8string upper = GitProbe::canonical_path(fs::path("SomeDir") / "README.MD");
    const std::u8string lower = GitProbe::canonical_path(fs::path("somedir") / "readme.md");
    CHECK_EQ(std::string(upper.begin(), upper.end()), std::string(lower.begin(), lower.end()));
}
#else
WX_TEST(git_probe, canonical_path_does_not_case_fold_on_posix) {
    // POSIX is case-sensitive: folding here would merge two different files.
    const std::u8string a = GitProbe::canonical_path(fs::path("SomeDir") / "README.MD");
    const std::u8string b = GitProbe::canonical_path(fs::path("somedir") / "readme.md");
    CHECK_NE(std::string(a.begin(), a.end()), std::string(b.begin(), b.end()));
}
#endif

WX_TEST(git_probe, canonical_path_preserves_non_ascii_names) {
    // The reason canonical_path returns UTF-8 rather than std::string: through
    // the ANSI code page a Korean filename comes back as mojibake, and every
    // cache key built from it misses forever.
    TempDir tmp;
    const fs::path korean = tmp.path() / fs::path(u8"\uD55C\uAE00\uD14C\uC2A4\uD2B8.txt");
    write_file(korean, "hangul content");

    const std::u8string canon = GitProbe::canonical_path(korean);
    const std::string s(reinterpret_cast<const char*>(canon.data()), canon.size());
    CHECK_MSG(s.find("\uD55C\uAE00") != std::string::npos,
              "Hangul must survive canonicalisation: " + s);

    // Round-trip: reconstruct a path from the canonical bytes and confirm it
    // still stats to the same file. THIS is the assertion that would have
    // caught the ANSI code-page bug — the string looked fine either way, but
    // the reconstructed path pointed at nothing.
    std::error_code ec;
    const fs::path round_tripped(canon);
    CHECK_MSG(fs::exists(round_tripped, ec), "canonical form must reconstruct a real path: " + s);

    const FileStamp original = GitProbe::stamp(korean);
    const FileStamp recovered = GitProbe::stamp(round_tripped);
    CHECK(original == recovered);
}

// --- parse_hex64 ----------------------------------------------------------

WX_TEST(git_probe, parse_hex64_handles_real_sha_shapes) {
    CHECK_EQ(wx::core::cache::parse_hex64("0123456789abcdef"), std::uint64_t{0x0123456789abcdefULL});
    CHECK_EQ(wx::core::cache::parse_hex64("ABCDEF"), std::uint64_t{0xABCDEF});
    CHECK_EQ(wx::core::cache::parse_hex64(""), std::uint64_t{0});
    // Stops at the first non-hex character rather than producing garbage.
    CHECK_EQ(wx::core::cache::parse_hex64("12g4"), std::uint64_t{0x12});
    // Longer than 16 digits truncates instead of wrapping silently.
    CHECK_EQ(wx::core::cache::parse_hex64("0123456789abcdef0123"), std::uint64_t{0x0123456789abcdefULL});
}

int main() {
    return ::wxtest::run_all();
}