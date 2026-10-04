// WUXICODE — minimal test harness.
//
// WHY NOT CATCH2 / GOOGLETEST: both are fetched at configure time by
// FetchContent or a submodule, which means `cmake -DWX_BUILD_TESTS=ON` fails on
// a machine with no network and no warm cache. A test suite that cannot be run
// offline cannot be run before a release cut, which is exactly when it matters.
//
// The feature set needed here is small enough to fit on one page: register a
// function, run it, report failures with file and line, exit non-zero. That is
// all of it, and an honest implementation is shorter than the CMake plumbing
// needed to vendor a framework.

#pragma once

#include <cstdio>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace wxtest {

struct TestCase {
    std::string_view suite;
    std::string_view name;
    void (*fn)();
};

inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> cases;
    return cases;
}

struct Registrar {
    Registrar(std::string_view suite, std::string_view name, void (*fn)()) {
        registry().push_back(TestCase{suite, name, fn});
    }
};

// Per-test failure counter. Reset by run_all before each case.
inline int& failure_count() {
    static int count = 0;
    return count;
}

inline void report_failure(const char* file, int line, std::string_view expr, std::string_view detail) {
    ++failure_count();
    std::printf("    FAIL %s:%d\n      expr: %.*s\n", file, line, static_cast<int>(expr.size()),
                expr.data());
    if (!detail.empty()) {
        std::printf("      %.*s\n", static_cast<int>(detail.size()), detail.data());
    }
}

template <typename T>
std::string describe(const T& value) {
    std::ostringstream os;
    os << value;
    return os.str();
}

inline int run_all() {
    int failed_tests = 0;
    std::string current_suite;

    for (const TestCase& tc : registry()) {
        if (tc.suite != current_suite) {
            current_suite = std::string(tc.suite);
            std::printf("\n[%s]\n", current_suite.c_str());
        }

        failure_count() = 0;
        std::printf("  %.*s ... ", static_cast<int>(tc.name.size()), tc.name.data());
        std::fflush(stdout);

        tc.fn();

        if (failure_count() == 0) {
            std::printf("ok\n");
        } else {
            std::printf("  (%d failure(s))\n", failure_count());
            ++failed_tests;
        }
    }

    std::printf("\n%zu test(s), %d failed\n", registry().size(), failed_tests);
    return failed_tests == 0 ? 0 : 1;
}

}  // namespace wxtest

#define WX_TEST(suite_name, test_name)                                                     \
    static void suite_name##_##test_name();                                                \
    static ::wxtest::Registrar suite_name##_##test_name##_registrar(                        \
        #suite_name, #test_name, &suite_name##_##test_name);                              \
    static void suite_name##_##test_name()

#define CHECK(expr)                                                                        \
    do {                                                                                   \
        if (!(expr)) {                                                                     \
            ::wxtest::report_failure(__FILE__, __LINE__, "CHECK(" #expr ")", "");           \
        }                                                                                  \
    } while (false)

#define CHECK_MSG(expr, msg)                                                               \
    do {                                                                                   \
        if (!(expr)) {                                                                     \
            ::wxtest::report_failure(__FILE__, __LINE__, "CHECK(" #expr ")", (msg));        \
        }                                                                                  \
    } while (false)

#define CHECK_EQ(actual, expected)                                                         \
    do {                                                                                   \
        const auto wx_a = (actual);                                                        \
        const auto wx_b = (expected);                                                      \
        if (!(wx_a == wx_b)) {                                                             \
            ::wxtest::report_failure(__FILE__, __LINE__, "CHECK_EQ(" #actual ", " #expected ")", \
                                     "actual: " + ::wxtest::describe(wx_a) +                \
                                         "  expected: " + ::wxtest::describe(wx_b));        \
        }                                                                                  \
    } while (false)

#define CHECK_NE(actual, unexpected)                                                       \
    do {                                                                                   \
        const auto wx_a = (actual);                                                        \
        const auto wx_b = (unexpected);                                                    \
        if (wx_a == wx_b) {                                                                \
            ::wxtest::report_failure(__FILE__, __LINE__, "CHECK_NE(" #actual ", " #unexpected ")", \
                                     "both: " + ::wxtest::describe(wx_a));                 \
        }                                                                                  \
    } while (false)

// Aborts the current test case without leaving a half-written fixture behind
// for the next one. Used only for setup failures, where continuing would
// produce noise that hides the real problem.
#define REQUIRE(expr)                                                                      \
    do {                                                                                   \
        if (!(expr)) {                                                                     \
            ::wxtest::report_failure(__FILE__, __LINE__, "REQUIRE(" #expr ")", "aborting test case"); \
            return;                                                                        \
        }                                                                                  \
    } while (false)