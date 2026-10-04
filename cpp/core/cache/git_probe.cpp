// WUXICODE — git working-tree probe. See git_probe.hpp for scope and rationale.

#include "core/cache/git_probe.hpp"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <fstream>
#include <ios>
#include <system_error>
#include <utility>
#include <vector>

namespace wx::core::cache {
namespace {

namespace fs = std::filesystem;

// Read a small file whole, capped. Returns false on any failure — callers treat
// an unreadable git file as "not applicable", never as an error.
bool read_small_file(const fs::path& path, std::string& out, std::size_t cap) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return false;
    }
    out.clear();
    out.resize(cap);
    in.read(out.data(), static_cast<std::streamsize>(cap));
    if (in.bad()) {
        return false;
    }
    out.resize(static_cast<std::size_t>(in.gcount()));
    return true;
}

void strip_eol(std::string& s) {
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ' || s.back() == '\t')) {
        s.pop_back();
    }
}

void lstrip(std::string& s) {
    std::size_t i = 0;
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) {
        ++i;
    }
    if (i != 0) {
        s.erase(0, i);
    }
}

bool starts_with(const std::string& s, const char* prefix) {
    const std::size_t n = std::char_traits<char>::length(prefix);
    return s.size() >= n && std::equal(prefix, prefix + n, s.begin());
}

// ASCII-only case fold. Deliberately NOT std::tolower with the default locale:
// that is UB for negative char values and, under a legacy code page, would
// corrupt the continuation bytes of a UTF-8 path. A Korean or Japanese path
// must survive canonicalisation unchanged, and ASCII A-Z is the only case that
// Windows itself folds for path comparison.
char ascii_lower(char c) noexcept {
    return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c;
}

// Find `refname` in packed-refs. Returns the sha text, or an empty string.
//
// Format (see git-pack-refs(5)):
//   # pack-refs with: peeled fully-peeled sorted
//   <40-hex> <refname>
//   ^<40-hex>            <- peeled tag target, annotates the line ABOVE
// A `^` line must not be matched as an entry of its own, or a tag's peeled
// sha would be reported as the sha of whatever ref happens to sort after it.
std::string lookup_packed_refs(const fs::path& gitdir, const std::string& refname) {
    const fs::path packed = gitdir / "packed-refs";
    std::ifstream in(packed, std::ios::binary);
    if (!in) {
        return {};
    }

    std::string line;
    while (std::getline(in, line)) {
        strip_eol(line);
        if (line.empty() || line[0] == '#' || line[0] == '^') {
            continue;
        }
        const std::size_t sp = line.find(' ');
        if (sp == std::string::npos) {
            continue;
        }
        std::string sha = line.substr(0, sp);
        std::string name = line.substr(sp + 1);
        lstrip(name);
        if (name == refname) {
            return sha;
        }
    }
    return {};
}

}  // namespace

std::uint64_t parse_hex64(std::string_view hex) noexcept {
    // A sha is 40 (or 64) hex digits; the cache only keeps 16. The truncation
    // must be explicit and fixed-width: letting the loop run until the value
    // happens to overflow would make the result depend on how far the shift
    // carried, which is a value nobody can reproduce by reading the sha.
    constexpr int kMaxHexDigits = 16;

    std::uint64_t out = 0;
    std::size_t i = 0;
    // Skip leading whitespace; git files often carry a trailing newline.
    while (i < hex.size() && (hex[i] == ' ' || hex[i] == '\t')) {
        ++i;
    }

    for (int digits = 0; i < hex.size() && digits < kMaxHexDigits; ++i, ++digits) {
        const char c = hex[i];
        std::uint64_t digit;
        if (c >= '0' && c <= '9') {
            digit = static_cast<std::uint64_t>(c - '0');
        } else if (c >= 'a' && c <= 'f') {
            digit = static_cast<std::uint64_t>(c - 'a' + 10);
        } else if (c >= 'A' && c <= 'F') {
            digit = static_cast<std::uint64_t>(c - 'A' + 10);
        } else {
            break;  // not a hex digit: stop, keep what parsed cleanly
        }
        out = (out << 4) | digit;
    }
    return out;
}

std::filesystem::path GitProbe::repo_root(const std::filesystem::path& start) {
    std::error_code ec;

    fs::path current = start;
    if (current.empty()) {
        current = fs::current_path(ec);
        if (ec) {
            return {};
        }
    }

    // Lexical normalisation FIRST: "a/./b" and "a/b/.." must not each cost an
    // extra hop, and a path like "C:\repo\src\.." should not report a root one
    // level shallower than the truth.
    current = current.lexically_normal();

    if (!current.is_absolute()) {
        const fs::path cwd = fs::current_path(ec);
        if (ec) {
            return {};
        }
        current = (cwd / current).lexically_normal();
    }

    for (int hop = 0; hop < kMaxParentHops; ++hop) {
        std::error_code probe_ec;
        // status() with an error_code: exists() without one throws on a
        // permission error, and "cannot stat" must read as "not a repo".
        if (fs::status(current / ".git", probe_ec).type() != fs::file_type::not_found) {
            return current;
        }

        const fs::path parent = current.parent_path();
        if (parent == current || parent.empty()) {
            break;  // reached the filesystem root
        }
        current = parent;
    }
    return {};
}

std::filesystem::path GitProbe::resolve_gitdir(const std::filesystem::path& repo_root_in) {
    std::error_code ec;
    const fs::path dot_git = repo_root_in / ".git";

    const fs::file_status st = fs::status(dot_git, ec);
    if (ec) {
        return {};
    }

    if (fs::is_directory(st)) {
        return dot_git;
    }

    if (!fs::is_regular_file(st)) {
        return {};
    }

    // `gitdir: <path>` — a linked worktree or submodule.
    std::string text;
    if (!read_small_file(dot_git, text, kMaxGitFileBytes)) {
        return {};
    }
    strip_eol(text);
    static const char kPrefix[] = "gitdir:";
    if (!starts_with(text, kPrefix)) {
        return {};
    }

    std::string target = text.substr(sizeof(kPrefix) - 1);
    lstrip(target);
    if (target.empty()) {
        return {};
    }

    fs::path p(target);
    if (!p.is_absolute()) {
        // Relative gitdir paths are relative to the repository root, not to the
        // .git file's own directory (which is the same directory in practice,
        // but resolving against repo_root_in is what git documents).
        p = repo_root_in / p;
    }
    return p.lexically_normal();
}

std::uint64_t GitProbe::head_sha(const std::filesystem::path& gitdir) {
    if (gitdir.empty()) {
        return 0;
    }

    std::string head;
    if (!read_small_file(gitdir / "HEAD", head, GitProbe::kMaxGitFileBytes)) {
        return 0;
    }
    strip_eol(head);

    static const char kRefPrefix[] = "ref:";
    if (starts_with(head, kRefPrefix)) {
        std::string refname = head.substr(sizeof(kRefPrefix) - 1);
        lstrip(refname);
        if (refname.empty()) {
            return 0;
        }

        // 1. Loose ref: git writes .git/<refname> as a file containing a sha.
        std::string sha_text;
        if (read_small_file(gitdir / fs::path(refname), sha_text, GitProbe::kMaxGitFileBytes)) {
            strip_eol(sha_text);
            const std::uint64_t sha = parse_hex64(sha_text);
            if (sha != 0) {
                return sha;
            }
        }

        // 2. Packed: refs live only in packed-refs once the repo has been gc'd.
        //    This is the common case on a clone, not an edge case.
        const std::string packed = lookup_packed_refs(gitdir, refname);
        if (!packed.empty()) {
            const std::uint64_t sha = parse_hex64(packed);
            if (sha != 0) {
                return sha;
            }
        }

        // 3. Unborn branch. `git init` with no commit yet. Legitimate state,
        //    reported as 0; the caller keeps going on the index axis alone.
        return 0;
    }

    // Detached HEAD: the file holds the sha itself.
    return parse_hex64(head);
}

std::uint64_t GitProbe::index_mtime(const std::filesystem::path& gitdir) {
    if (gitdir.empty()) {
        return 0;
    }
    std::error_code ec;
    const auto ft = fs::last_write_time(gitdir / "index", ec);
    if (ec) {
        return 0;
    }
    // file_clock ticks. Units are implementation-defined and only need to be
    // stable within this process — see FileStamp in the header.
    const auto ticks = ft.time_since_epoch().count();
    return ticks < 0 ? 0u : static_cast<std::uint64_t>(ticks);
}

GitRevision GitProbe::revision(const std::filesystem::path& start) {
    GitRevision out;

    const fs::path root = repo_root(start);
    if (root.empty()) {
        return out;
    }
    out.valid = true;

    const fs::path gitdir = resolve_gitdir(root);
    if (gitdir.empty()) {
        return out;  // valid = true, head/index both 0
    }

    out.head_sha = head_sha(gitdir);
    out.index_mtime_ns = index_mtime(gitdir);
    return out;
}

FileStamp GitProbe::stamp(const std::filesystem::path& path) {
    FileStamp out;
    if (path.empty()) {
        return out;
    }

    std::error_code ec;
    const fs::file_status st = fs::status(path, ec);
    if (ec || !fs::exists(st)) {
        return out;  // exists = false; caller treats this as a normal state
    }

    out.exists = true;
    out.is_directory = fs::is_directory(st);

    const auto ft = fs::last_write_time(path, ec);
    if (!ec) {
        const auto ticks = ft.time_since_epoch().count();
        out.mtime_ns = ticks < 0 ? 0u : static_cast<std::uint64_t>(ticks);
    }

    if (!out.is_directory) {
        const auto sz = fs::file_size(path, ec);
        if (!ec) {
            out.size = static_cast<std::uint64_t>(sz);
        }
    }
    return out;
}

std::u8string GitProbe::canonical_path(const std::filesystem::path& path) {
    if (path.empty()) {
        return {};
    }

    std::error_code ec;
    fs::path p = path;
    if (!p.is_absolute()) {
        const fs::path cwd = fs::current_path(ec);
        if (ec) {
            // No current directory to resolve against. Return the path as given
            // rather than dropping it: a non-canonical key is merely less
            // effective, whereas an empty one silently disables the axis.
            return fs::path(path).generic_u8string();
        }
        p = cwd / p;
    }
    p = p.lexically_normal();

    std::u8string s = p.generic_u8string();  // UTF-8, '/' separators everywhere
    if (s.empty()) {
        return s;
    }

#ifdef _WIN32
    // Case-fold so two spellings of one NTFS name share a cache entry.
    // Byte-wise over UTF-8: every byte of a multi-byte sequence is >= 0x80, so
    // only real ASCII A-Z is touched and a Korean or Japanese path passes
    // through unchanged.
    for (char8_t& c : s) {
        c = static_cast<char8_t>(ascii_lower(static_cast<char>(c)));
    }

    // Drop a trailing separator, but never reduce a root to empty: "C:/" must
    // stay "C:/" because that is the repository root in the canonical form.
    while (s.size() > 1 && (s.back() == u8'/' || s.back() == u8'\\')) {
        if (s.size() == 3 && s[1] == u8':') {
            break;  // "C:/"
        }
        s.pop_back();
    }
    if (s.empty()) {
        s = u8"/";
    }
#else
    // POSIX is case-sensitive. Folding here would merge two genuinely
    // different files, which is worse than a redundant cache entry.
    while (s.size() > 1 && s.back() == '/') {
        s.pop_back();
    }
#endif

    return s;
}

}  // namespace wx::core::cache