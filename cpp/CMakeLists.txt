# WUXICODE — C++ core + pybind11 module

# ---------------------------------------------------------------------------
# wxcore: static library, no third-party dependencies.
# C++ computes. Python decides. Nothing in here selects a model, orders a
# tool call, or parses an OpenAI schema.
# ---------------------------------------------------------------------------

add_library(wxcore STATIC
  core/base/hash.hpp
  core/net/sse_parser.hpp
  core/net/sse_parser.cpp
  core/text/tokenizer_heuristic.hpp
  core/text/tokenizer_heuristic.cpp
)

target_include_directories(wxcore
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:include/wuxicode>
)

target_compile_features(wxcore PUBLIC cxx_std_${WX_CXX_STANDARD})

if(MSVC)
  target_compile_options(wxcore PRIVATE /W4 /utf-8 /permissive- /EHsc)
else()
  target_compile_options(wxcore PRIVATE -Wall -Wextra -Wpedantic)
endif()

# ---------------------------------------------------------------------------
# _wx_core: the single pybind11 module imported by Python.
#
# Target name comes from version.json (extension_module) so the module name
# lives in exactly one place.
# ---------------------------------------------------------------------------

find_package(pybind11 CONFIG QUIET)
if(NOT pybind11_FOUND)
  message(FATAL_ERROR
    "pybind11 CMake package not found.\n"
    "  Normal build : pip install -e .\n"
    "  Standalone   : pip install pybind11, then -Dpybind11_DIR=<prefix>/share/cmake/pybind11")
endif()

pybind11_add_module(${WX_EXTENSION_MODULE}
  bindings/wx_core.cpp
)

target_link_libraries(${WX_EXTENSION_MODULE} PRIVATE wxcore)

# pybind11 headers are third-party: keep the project's own warning level off them.
if(MSVC)
  target_compile_options(${WX_EXTENSION_MODULE} PRIVATE /utf-8 /permissive- /EHsc /bigobj)
else()
  target_compile_options(${WX_EXTENSION_MODULE} PRIVATE -Wall -Wextra)
endif()

# Identity is baked in from version.json so `wx_core.version()` cannot drift
# from the Python package version.
target_compile_definitions(${WX_EXTENSION_MODULE} PRIVATE
  WX_VERSION_STRING="${WX_VERSION}"
  WX_CHANNEL_STRING="${WX_CHANNEL}"
  WX_NAME_STRING="${WX_NAME}"
  WX_LICENSE_STRING="${WX_LICENSE_SPDX}"
)

# Scikit-build-core copies this into the wheel root so `import _wx_core` works.
# The destination MUST stay "." (mirrors wheel.install-dir = "" in pyproject).
install(TARGETS ${WX_EXTENSION_MODULE}
        LIBRARY DESTINATION .
        RUNTIME DESTINATION .
        ARCHIVE DESTINATION .)

# ---------------------------------------------------------------------------
# tests (1B)
# ---------------------------------------------------------------------------

if(WX_BUILD_TESTS)
  if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt")
    message(FATAL_ERROR "WX_BUILD_TESTS=ON but cpp/tests/CMakeLists.txt does not exist yet")
  endif()
  enable_testing()
  add_subdirectory(tests)
endif()