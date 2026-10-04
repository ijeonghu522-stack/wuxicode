# WUXICODE — C++ unit tests (Phase 1B)
#
# Plain executables + CTest. No test framework: see cpp/tests/harness.hpp for
# why a FetchContent-based framework was rejected (it makes the suite
# unrunnable offline, i.e. exactly when a release cut needs it).
#
# Each test binary owns its own main() and calls wxtest::run_all(). They link
# wxcore, not _wx_core, so a cache bug can be reproduced without involving
# pybind11, the GIL, or a Python interpreter.

set(WX_TEST_TARGETS)

# Paths are relative to cpp/tests/. The `#include "tests/harness.hpp"` form still
# works because wxcore exports cpp/ as a PUBLIC include directory.
function(wx_add_test test_name)
  add_executable(${test_name} ${ARGN} harness.hpp fixtures.hpp)
  target_link_libraries(${test_name} PRIVATE wxcore)
  add_test(NAME ${test_name} COMMAND ${test_name})
  set(WX_TEST_TARGETS ${WX_TEST_TARGETS} ${test_name} PARENT_SCOPE)
endfunction()

wx_add_test(test_lru test_lru.cpp)
wx_add_test(test_git_probe test_git_probe.cpp)
wx_add_test(test_tool_cache test_tool_cache.cpp)

# Warnings and source encoding match wxcore's own settings. /utf-8 is not
# optional: test_git_probe.cpp contains Hangul path literals, and without it MSVC
# reads them as the system ANSI code page, which turns those tests into
# tautologies — the strings would be compared after mangling on both sides.
foreach(target IN LISTS WX_TEST_TARGETS)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /utf-8 /permissive- /EHsc)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
  endif()
endforeach()