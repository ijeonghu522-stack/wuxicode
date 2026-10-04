// WUXICODE — shared test fixtures.
//
// Deliberately minimal and git-binary-free. See test_git_probe.cpp for why the
// git-shaped directories are hand-built rather than created with `git init`.

#pragma once

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>

namespace wxtest {

namespace fs = std::filesystem;

// A unique temp directory that removes itself on scope exit.
//
// Under the SYSTEM TEMP DIRECTORY, never inside the working tree: a fixture
// placed inside this repository would let repo_root() walk up and find the real
// .git, and every "this is not a repository" assertion would then pass for
// entirely the wrong reason.
class TempDir {
public:
    TempDir() {
        static int counter = 0;
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        path_ = fs::temp_directory_path() /
                ("wuxicode_test_" + std::to_string(stamp) + "_" + std::to_string(++counter));
        fs::create_directories(path_);
    }

    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }

    TempDir(const TempDir&) = delete;
    TempDir& operator=(const TempDir&) = delete;

    [[nodiscard]] const fs::path& path() const noexcept { return path_; }
    [[nodiscard]] fs::path operator/(const char* rel) const { return path_ / rel; }

private:
    fs::path path_;
};

inline void write_file(const fs::path& p, const std::string& content) {
    std::error_code ec;
    fs::create_directories(p.parent_path(), ec);
    std::ofstream out(p, std::ios::binary | std::ios::trunc);
    out << content;
}

// Move a file's mtime to a value guaranteed different from the current one.
//
// Used instead of sleeping. A sleep-based test is either slow or flaky, and
// what is under test is that the axis MOVES, not that wall-clock time passed.
inline bool force_new_mtime(const fs::path& p, int seconds_ahead = 30) {
    std::error_code ec;
    const auto bumped = fs::last_write_time(p) + std::chrono::seconds(seconds_ahead);
    fs::last_write_time(p, bumped, ec);
    return !ec;
}

// A repository with a real HEAD file, a real loose ref, and a real index.
inline fs::path make_repo(const TempDir& tmp, const std::string& sha, const char* name = "repo") {
    const fs::path root = tmp.path() / name;
    write_file(root / ".git" / "HEAD", "ref: refs/heads/main\n");
    write_file(root / ".git" / "refs" / "heads" / "main", sha + "\n");
    write_file(root / ".git" / "index", "DIRC fake index contents");
    return root;
}

}  // namespace wxtest