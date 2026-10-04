# WUXICODE — root CMakeLists
#
# Reads version.json and derives every build-relevant value from it.
# Hard rule: unknown keys in version.json are IGNORED, never fatal.
# Missing or malformed REQUIRED keys are fatal.
#
# CONTRACT (added keys to version.json must not require editing this file):
#   wx_json_require() pulls keys one at a time. We never enumerate the
#   document, so new keys are structurally invisible to this file.

cmake_minimum_required(VERSION 3.24)

# ---------------------------------------------------------------------------
# version.json -> build variables
# ---------------------------------------------------------------------------

set(WX_VERSION_FILE "${CMAKE_CURRENT_SOURCE_DIR}/version.json")

if(NOT EXISTS "${WX_VERSION_FILE}")
  message(FATAL_ERROR "version.json not found at: ${WX_VERSION_FILE}")
endif()

file(READ "${WX_VERSION_FILE}" WX_VERSION_RAW)

# A CMake variable holding a CACHE INTERNAL value gets truncated at the first
# newline, which silently produces a half-parsed JSON document and a misleading
# "NOTFOUND" on the first key. Newlines are whitespace as far as JSON is
# concerned, so collapse them before the document ever reaches string(JSON).
string(REPLACE "\n" " " WX_VERSION_JSON "${WX_VERSION_RAW}")
string(REPLACE "\r" " " WX_VERSION_JSON "${WX_VERSION_JSON}")
string(REPLACE "\t" " " WX_VERSION_JSON "${WX_VERSION_JSON}")

# Pull exactly one key. Fatal only when THAT key is absent or malformed.
#
# string(JSON) sets ERROR_VARIABLE to the literal string NOTFOUND on success,
# not to an empty string. Testing against "" makes every lookup fail, which is
# why both forms are rejected here.
function(wx_json_require OUT_VAR KEY)
  string(JSON _wx_val ERROR_VARIABLE _wx_err GET "${WX_VERSION_JSON}" ${KEY})
  if(NOT (_wx_err STREQUAL "NOTFOUND" OR _wx_err STREQUAL ""))
    message(FATAL_ERROR
      "version.json: required key '${KEY}' is missing or malformed.\n"
      "  detail : ${_wx_err}\n"
      "  file   : ${WX_VERSION_FILE}\n"
      "  hint   : add \"${KEY}\" to version.json, or drop the requirement\n"
      "           in CMakeLists.txt if it is no longer needed.")
  endif()
  if(_wx_val MATCHES "-NOTFOUND$")
    message(FATAL_ERROR "version.json: required key '${KEY}' resolved to NOTFOUND")
  endif()
  set(${OUT_VAR} "${_wx_val}" PARENT_SCOPE)
endfunction()

# Nested pull, e.g. wx_json_require(SPDX LICENSE_SPDX "license" "spdx")
function(wx_json_require_nested OUT_VAR KEY SUBKEY)
  string(JSON _wx_val ERROR_VARIABLE _wx_err GET "${WX_VERSION_JSON}" ${KEY} ${SUBKEY})
  if(NOT (_wx_err STREQUAL "NOTFOUND" OR _wx_err STREQUAL ""))
    message(FATAL_ERROR
      "version.json: required key '${KEY}.${SUBKEY}' is missing or malformed.\n"
      "  detail : ${_wx_err}\n"
      "  file   : ${WX_VERSION_FILE}")
  endif()
  set(${OUT_VAR} "${_wx_val}" PARENT_SCOPE)
endfunction()

wx_json_require(WX_NAME             "name")
wx_json_require(WX_SLUG             "slug")
wx_json_require(WX_VERSION          "version")
wx_json_require(WX_CHANNEL          "channel")
wx_json_require(WX_PYTHON_PACKAGE   "python_package")
wx_json_require(WX_EXTENSION_MODULE "extension_module")
wx_json_require(WX_PYTHON_REQUIRES  "python_requires")
wx_json_require(WX_CMAKE_MINIMUM    "cmake_minimum")
wx_json_require(WX_CXX_STANDARD     "cxx_standard")
wx_json_require(WX_MSVC_RUNTIME     "msvc_runtime")
wx_json_require_nested(WX_LICENSE_SPDX "license" "spdx")

# ---------------------------------------------------------------------------
# version.json self-consistency
# ---------------------------------------------------------------------------

# cmake_minimum_required() above is hardcoded at 3.24 because we cannot read
# a file before the language standard is established. version.json is the
# authority for the *floor*; verify we are not below it.
if(NOT CMAKE_VERSION VERSION_GREATER_EQUAL "${WX_CMAKE_MINIMUM}")
  message(FATAL_ERROR
    "CMake ${CMAKE_VERSION} is older than version.json cmake_minimum "
    "(${WX_CMAKE_MINIMUM}). Upgrade CMake.")
endif()

if(NOT CMAKE_CXX_COMPILER_ID MATCHES "MSVC|GNU|Clang")
  message(FATAL_ERROR "Unsupported compiler: '${CMAKE_CXX_COMPILER_ID}'")
endif()

if(MSVC AND NOT WX_MSVC_RUNTIME MATCHES "^(MD|MT)$")
  message(FATAL_ERROR "version.json msvc_runtime must be \"MD\" or \"MT\", got \"${WX_MSVC_RUNTIME}\"")
endif()

message(STATUS "WUXICODE ${WX_VERSION} (${WX_CHANNEL})  cpp=${WX_CXX_STANDARD}  crt=${WX_MSVC_RUNTIME}")

# ---------------------------------------------------------------------------
# language / runtime
# ---------------------------------------------------------------------------

set(CMAKE_CXX_STANDARD          ${WX_CXX_STANDARD})
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS        OFF)

set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Multi-threaded DLL = /MD. Static CRT in a Python extension module is unsafe:
# the interpreter already has the DLL CRT loaded and mixing allocators in one
# process produces intermittent heap corruption.
if(MSVC)
  if(WX_MSVC_RUNTIME STREQUAL "MD")
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
  else()
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  endif()

  add_compile_definitions(
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
    _CRT_SECURE_NO_WARNINGS
  )
endif()

# No global /W4: pybind11 headers are third-party and will spam. Warnings are
# applied per target in cpp/CMakeLists.txt.
if(NOT MSVC)
  add_compile_options(-Wall -Wextra)
endif()

# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------

option(WX_BUILD_TESTS "Build C++ unit tests (1B)" OFF)

# enable_testing() must be called from the TOP-LEVEL list file. Calling it only
# inside cpp/ registers the tests in that subdirectory's CTestTestfile, and a
# `ctest` run from the build root then reports "No tests were found" while the
# test executables sit right there having passed.
if(WX_BUILD_TESTS)
  enable_testing()
endif()

# ---------------------------------------------------------------------------
# subdirectories
# ---------------------------------------------------------------------------

add_subdirectory(cpp)

# ---------------------------------------------------------------------------
# install (Python sources are installed by scikit-build-core, NOT here)
# ---------------------------------------------------------------------------

install(TARGETS wxcore
        ARCHIVE DESTINATION lib
        LIBRARY DESTINATION lib
        RUNTIME DESTINATION bin)

install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/cpp/"
        DESTINATION include/wuxicode
        FILES_MATCHING PATTERN "*.hpp")