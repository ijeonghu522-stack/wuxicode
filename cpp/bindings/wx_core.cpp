// WUXICODE — pybind11 bindings for wxcore.
//
// One module, `_wx_core`. Everything Python touches goes through here.
//
// BOUNDARY RULE (docs/adr/0001-sse-boundary.md): this layer translates types
// and nothing else. It does not parse JSON, does not know about OpenAI, does
// not decide anything. Every function below is either a pure computation or a
// thin wrapper over a wxcore class.

// Python.h first: the UTF-8 helpers below call PyUnicode_AsUTF8AndSize and
// PyUnicode_DecodeUTF8 directly, and relying on pybind11 to have pulled the C
// API in transitively is a dependency on an implementation detail.
#include <Python.h>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "core/base/hash.hpp"
#include "core/cache/git_probe.hpp"
#include "core/cache/lru.hpp"
#include "core/cache/tool_cache.hpp"
#include "core/net/sse_parser.hpp"
#include "core/text/tokenizer_heuristic.hpp"

#ifndef WX_VERSION_STRING
#define WX_VERSION_STRING "0.0.0-unbuilt"
#endif
#ifndef WX_CHANNEL_STRING
#define WX_CHANNEL_STRING "unknown"
#endif
#ifndef WX_NAME_STRING
#define WX_NAME_STRING "WUXICODE"
#endif
#ifndef WX_LICENSE_STRING
#define WX_LICENSE_STRING "unknown"
#endif

namespace py = pybind11;

using wx::core::net::SseEvent;
using wx::core::net::SseParser;
using wx::core::text::HeuristicCalibration;

using wx::core::cache::CacheDescriptor;
using wx::core::cache::FileStamp;
using wx::core::cache::GitProbe;
using wx::core::cache::GitRevision;
using wx::core::cache::LruCache;
using wx::core::cache::LruStats;
using wx::core::cache::ToolCache;
using wx::core::cache::ToolCacheStats;

namespace {

// str and bytes both land here. bytes must NOT be utf-8 decoded: SSE payloads
// are framed bytes, and a chunk boundary can sit inside a code point.
// Takes py::handle, not py::object: iterating py::args yields handles.
std::string as_bytes(const py::handle& obj) {
    if (py::isinstance<py::str>(obj) || py::isinstance<py::bytes>(obj)) {
        return obj.cast<std::string>();
    }
    throw py::type_error(
        "expected str or bytes, got " +
        py::str(py::type::handle_of(obj)).cast<std::string>());
}

// --- UTF-8 <-> Python str -------------------------------------------------
//
// Cache paths and arguments are std::u8string. They have to cross into Python
// as `str` without a round trip through the ANSI code page: a Korean path that
// goes str -> bytes -> str as cp949 comes back as different characters, and a
// cache keyed on it would miss on every single call.
//
// Bytes are accepted wherever a str is, and are taken as UTF-8 verbatim. That
// is what os.fsencode and friends hand back, so callers that already hold raw
// path bytes do not have to decode them just to call in here.

py::str u8_to_py(const std::u8string& s) {
    return py::reinterpret_steal<py::str>(PyUnicode_DecodeUTF8(
        reinterpret_cast<const char*>(s.data()), static_cast<Py_ssize_t>(s.size()), "strict"));
}

std::u8string py_to_u8(const py::handle& obj) {
    if (py::isinstance<py::bytes>(obj)) {
        const std::string raw = obj.cast<std::string>();
        return std::u8string(reinterpret_cast<const char8_t*>(raw.data()), raw.size());
    }
    if (!py::isinstance<py::str>(obj)) {
        throw py::type_error("expected str or bytes, got " +
                             py::str(py::type::handle_of(obj)).cast<std::string>());
    }
// Two arguments, not three: the buffer is RETURNED, and only the length is an
// out-parameter. Returns nullptr on failure with a Python exception set.
Py_ssize_t len = 0;
const char* buf = PyUnicode_AsUTF8AndSize(obj.ptr(), &len);
if (buf == nullptr) {
    throw py::error_already_set();
}
return std::u8string(reinterpret_cast<const char8_t*>(buf), static_cast<std::size_t>(len));
}

std::filesystem::path py_to_path(const py::handle& obj) {
    if (py::isinstance<py::bytes>(obj)) {
        const std::string raw = obj.cast<std::string>();
        return std::filesystem::path(std::u8string(reinterpret_cast<const char8_t*>(raw.data()),
                                                    raw.size()));
    }
    if (py::isinstance<py::str>(obj)) {
        return std::filesystem::path(py_to_u8(obj));
    }

    // Anything else os.fspath() accepts: a pathlib.Path, or any registered
    // os.PathLike. Delegating to os.fspath is the correct move rather than
    // duck-typing __fspath__ here — it is the same protocol Python itself uses.
    py::object as_str;
    {
        py::gil_scoped_acquire acquire;
        try {
            as_str = py::module_::import("os").attr("fspath")(obj);
        } catch (const py::error_already_set&) {
            throw py::type_error("expected a path-like object, got " +
                                 py::str(py::type::handle_of(obj)).cast<std::string>());
        }
    }
    return std::filesystem::path(py_to_u8(as_str));
}

// Cache values are opaque bytes, NOT text. A `read` of a binary file is a
// legitimate result, and decoding it here would either corrupt it or raise on
// data the caller is equipped to handle. Bytes in, bytes out; Python decides.
py::bytes as_pybytes(const std::string& s) {
    return py::bytes(s);
}

py::object optional_bytes(bool hit, const std::string& value) {
    if (!hit) {
        return py::none();
    }
    return py::bytes(value);
}

py::dict lru_stats_to_dict(const LruStats& s) {
    py::dict d;
    d["hits"] = s.hits;
    d["misses"] = s.misses;
    d["inserts"] = s.inserts;
    d["updates"] = s.updates;
    d["evictions"] = s.evictions;
    d["erased"] = s.erased;
    d["rejected"] = s.rejected;
    d["bytes_resident"] = s.bytes_resident;
    d["entries"] = static_cast<std::uint64_t>(s.entries);
    return d;
}

py::dict file_stamp_to_dict(const FileStamp& s) {
    py::dict d;
    d["mtime_ns"] = s.mtime_ns;
    d["size"] = s.size;
    d["exists"] = s.exists;
    d["is_directory"] = s.is_directory;
    return d;
}

py::dict revision_to_dict(const GitRevision& r) {
    py::dict d;
    d["head_sha"] = r.head_sha;
    d["head_sha_hex"] = wx::core::to_hex16(r.head_sha);
    d["index_mtime_ns"] = r.index_mtime_ns;
    d["valid"] = r.valid;
    return d;
}

py::dict tool_cache_stats_to_dict(const ToolCacheStats& s) {
    py::dict d;
    d["lru"] = lru_stats_to_dict(s.lru);
    d["key_builds"] = s.key_builds;
    d["path_invalidations"] = s.path_invalidations;
    d["invalidate_calls"] = s.invalidate_calls;
    d["git_repo_found"] = s.git_repo_found;
    d["head_sha"] = s.head_sha;
    d["head_sha_hex"] = wx::core::to_hex16(s.head_sha);
    d["index_mtime_ns"] = s.index_mtime_ns;
    return d;
}

py::dict event_to_dict(const SseEvent& ev) {
    py::dict d;
    d["event"] = ev.event;
    d["data"] = ev.data;
    d["id"] = ev.id;
    d["retry"] = ev.retry;
    d["has_retry"] = ev.has_retry;
    return d;
}

// Parse with the GIL released: this sits in the hot path of every token and
// holds no Python state. The buffer is a local std::string, so releasing the
// interpreter lock cannot invalidate it.
py::list feed_and_take(SseParser& self, const py::object& chunk) {
    const std::string buf = as_bytes(chunk);

    std::vector<SseEvent> events;
    {
        py::gil_scoped_release release;
        events = self.feed_and_take(std::string_view(buf));
    }

    py::list out;
    for (const auto& ev : events) {
        out.append(event_to_dict(ev));
    }
    return out;
}

void finish(SseParser& self) {
    py::gil_scoped_release release;
    self.finish();
}

py::list take(SseParser& self) {
    std::vector<SseEvent> events;
    {
        py::gil_scoped_release release;
        events = self.take();
    }
    py::list out;
    for (const auto& ev : events) {
        out.append(event_to_dict(ev));
    }
    return out;
}

py::dict parser_stats(const SseParser& self) {
    py::dict d;
    d["events_emitted"] = self.events_emitted();
    d["comments_seen"] = self.comments_seen();
    d["unknown_fields"] = self.unknown_fields();
    d["bytes_consumed"] = self.bytes_consumed();
    d["buffered"] = self.buffered();
    d["trailing_cr"] = self.trailing_cr();
    d["last_id"] = self.last_id();
    return d;
}

std::uint64_t count_tokens(const std::string& text) {
    py::gil_scoped_release release;
    return wx::core::text::count(text);
}

double estimate_tokens(const std::string& text) {
    py::gil_scoped_release release;
    return wx::core::text::estimate(text);
}

std::uint64_t hash64(const std::string& data) {
    py::gil_scoped_release release;
    return wx::core::hash64(data);
}

// Order-independent combination of N byte fields into one canonical key.
// A cache key is assembled from several axes (tool, args, revision, mtime);
// none of them should be privileged by argument position.
std::string hash_key(const py::args& parts) {
    std::uint64_t acc = wx::core::kFnvOffsetBasis;
    for (const auto& part : parts) {
        const std::string s = as_bytes(part);
        acc = wx::core::hash64_combine(wx::core::hash64(s), acc);
    }
    return wx::core::to_hex16(acc);
}

py::dict calibration() {
    const HeuristicCalibration c{};
    py::dict d;
    d["ascii_chars_per_token"] = c.ascii_chars_per_token;
    d["whitespace_chars_per_token"] = c.whitespace_chars_per_token;
    d["cjk_chars_per_token"] = c.cjk_chars_per_token;
    d["other_chars_per_token"] = c.other_chars_per_token;
    d["hex_chars_per_token"] = c.hex_chars_per_token;
    d["base64_chars_per_token"] = c.base64_chars_per_token;
    d["hex_run_min"] = static_cast<std::uint64_t>(c.hex_run_min);
    d["base64_run_min"] = static_cast<std::uint64_t>(c.base64_run_min);
    return d;
}

// --- cache bindings -------------------------------------------------------
//
// The GIL is released around every call that touches the filesystem or takes
// the cache lock. The whole premise of this module is that READ tools run
// concurrently (ADR 0002), and a binding that holds the GIL across a cache
// lookup re-serialises exactly the work the design exists to parallelise.

py::object lru_get(LruCache& self, std::uint64_t key) {
    std::string out;
    bool hit = false;
    {
        py::gil_scoped_release release;
        hit = self.get(key, out);
    }
    return optional_bytes(hit, out);
}

bool lru_put(LruCache& self, std::uint64_t key, const std::string& value) {
    py::gil_scoped_release release;
    return self.put(key, value);
}

bool lru_contains(LruCache& self, std::uint64_t key) {
    py::gil_scoped_release release;
    return self.contains(key);
}

py::object tool_cache_get(ToolCache& self, const CacheDescriptor& d) {
    std::string out;
    bool hit = false;
    {
        py::gil_scoped_release release;
        hit = self.get(d, out);
    }
    return optional_bytes(hit, out);
}

bool tool_cache_put(ToolCache& self, const CacheDescriptor& d, const std::string& value) {
    py::gil_scoped_release release;
    return self.put(d, value);
}

std::uint64_t tool_cache_key_for(ToolCache& self, const CacheDescriptor& d) {
    py::gil_scoped_release release;
    return self.key_for(d);
}

std::size_t tool_cache_invalidate_path(ToolCache& self, const py::object& path) {
    // Converted before the lock: py_to_u8 can throw, and throwing with the GIL
    // released would unwind past a lock the object still owns.
    const std::u8string canonical = py_to_u8(path);
    py::gil_scoped_release release;
    return self.invalidate_path(canonical);
}

py::dict tool_cache_stats(const ToolCache& self) {
    // Probe into a POD first, then build the dict with the GIL HELD.
    //
    // Declaring the py::dict OUTSIDE the released scope looks like a harmless
    // detail and is not one: constructing a dict touches the refcount of every
    // key and value in it. Doing that with the GIL released races against every
    // other thread's refcount traffic, which under load is an access violation
    // rather than a wrong answer. It reproduces reliably at eight threads and
    // not at one, which is what makes it the kind of bug that ships.
    ToolCacheStats snapshot;
    {
        py::gil_scoped_release release;
        snapshot = self.stats();  // re-probes HEAD and the index: I/O-bound
    }
    return tool_cache_stats_to_dict(snapshot);
}

py::str tool_cache_canonical(ToolCache& self, const py::object& path) {
    const std::filesystem::path p = py_to_path(path);
    std::u8string out;
    {
        py::gil_scoped_release release;
        out = self.canonical(p);
    }
    return u8_to_py(out);
}

py::dict tool_cache_revision(const ToolCache& self) {
    // Same rule as tool_cache_stats: build Python objects only after the GIL
    // comes back. Probe into a POD inside the released scope instead.
    GitRevision rev;
    {
        py::gil_scoped_release release;
        rev = self.revision();
    }
    return revision_to_dict(rev);
}

py::str git_canonical_path(const py::object& path) {
    const std::filesystem::path p = py_to_path(path);
    std::u8string out;
    {
        py::gil_scoped_release release;
        out = GitProbe::canonical_path(p);
    }
    return u8_to_py(out);
}

py::dict git_stamp(const py::object& path) {
    const std::filesystem::path p = py_to_path(path);
    FileStamp st;
    {
        py::gil_scoped_release release;
        st = GitProbe::stamp(p);
    }
    return file_stamp_to_dict(st);
}

py::dict git_revision(const py::object& start) {
    const std::filesystem::path p = py_to_path(start);
    GitRevision rev;
    {
        py::gil_scoped_release release;
        rev = GitProbe::revision(p);
    }
    return revision_to_dict(rev);
}

py::str git_repo_root(const py::object& start) {
    const std::filesystem::path p = py_to_path(start);
    std::filesystem::path root;
    {
        py::gil_scoped_release release;
        root = GitProbe::repo_root(p);
    }
    return u8_to_py(root.generic_u8string());
}

std::uint64_t git_head_sha(const py::object& gitdir) {
    const std::filesystem::path p = py_to_path(gitdir);
    py::gil_scoped_release release;
    return GitProbe::head_sha(p);
}

std::uint64_t git_index_mtime(const py::object& gitdir) {
    const std::filesystem::path p = py_to_path(gitdir);
    py::gil_scoped_release release;
    return GitProbe::index_mtime(p);
}

}  // namespace

PYBIND11_MODULE(_wx_core, m) {
    m.doc() = "WUXICODE native core: SSE framing, token counting, cache keys.";
    m.attr("__version__") = WX_VERSION_STRING;

    // --- identity ------------------------------------------------------
    m.def("version", []() { return std::string(WX_VERSION_STRING); },
          "Package version, baked in from version.json at compile time.");
    m.def("channel", []() { return std::string(WX_CHANNEL_STRING); },
          "Release channel from version.json.");
    m.def("product", []() { return std::string(WX_NAME_STRING); },
          "Product name from version.json.");
    m.def("license", []() { return std::string(WX_LICENSE_STRING); },
          "SPDX license id from version.json.");

    // --- hashing -------------------------------------------------------
    m.def("hash64", &hash64, py::arg("data"),
          "Stable 64-bit hash. Identical across runs, processes and platforms.",
          py::call_guard<py::gil_scoped_release>());
    // No py::arg() annotation: a function taking py::args already consumes every
    // positional argument, and annotating it makes pybind11 3.x dispatch
    // ambiguous.
    m.def("hash_key", &hash_key,
          "Order-independent 16-hex-char key from N byte fields. "
          "Cache keys are assembled from several axes; argument order must not matter.");

    // --- token counting -------------------------------------------------
    m.def("count_tokens", &count_tokens, py::arg("text"),
          "Token count. Exact only when tokenizer_mode() is 'exact'; today it is "
          "a character-class heuristic.",
          py::call_guard<py::gil_scoped_release>());
    m.def("estimate_tokens", &estimate_tokens, py::arg("text"),
          "Fractional token estimate.",
          py::call_guard<py::gil_scoped_release>());
    m.def("tokenizer_mode",
          []() { return std::string(wx::core::text::mode_name(wx::core::text::TokenizerMode::Heuristic)); },
          "Either 'exact' or 'heuristic'. The status bar must display which one is "
          "live; a heuristic number presented as exact is a budgeting lie.");
    m.def("tokenizer_calibration", &calibration,
          "Active heuristic constants, for calibration tooling and tests.");

    // --- SSE framing -----------------------------------------------------
    py::class_<SseParser>(m, "SseParser",
                          "Incremental SSE framer. Bytes in, complete events out. "
                          "Knows nothing about the payload format.")
        .def(py::init<>())
        .def(
            "feed", &feed_and_take, py::arg("chunk"),
            "Consume a chunk and return the events it completed. Chunks may split "
            "anywhere: mid-line, between CR and LF, mid-UTF-8 code point.")
        .def("finish", &finish,
             "Flush a tail event when the stream ends without a final blank line.")
        .def("take", &take, "Drain queued events without feeding.")
        .def("reset", &SseParser::reset, "Clear all state and counters.")
        .def("stats", &parser_stats, "Framing counters for the status bar and diagnostics.")
        .def_property_readonly("buffered", &SseParser::buffered,
                               "Bytes of an incomplete line held internally.")
        .def_property_readonly("events_emitted", &SseParser::events_emitted);

    // --- git probes -------------------------------------------------------
    // Free functions rather than a class: GitProbe is stateless, so there is no
    // instance for Python to own and nothing to keep alive.
    m.def("git_canonical_path", &git_canonical_path, py::arg("path"),
          "Absolute, normalised, separator-normalised, case-folded (Windows) form of a path. "
          "Round-trips losslessly through non-ASCII names.");
    m.def("git_stamp", &git_stamp, py::arg("path"),
          "stat a path: {mtime_ns, size, exists, is_directory}. Missing paths report "
          "exists=False rather than raising.");
    m.def("git_revision", &git_revision, py::arg("start"),
          "Working-tree state at `start`: {head_sha, index_mtime_ns, valid}. "
          "valid=False means `start` is not inside a repository, which is a normal "
          "state and never an error.");
    m.def("git_repo_root", &git_repo_root, py::arg("start"),
          "Nearest ancestor containing a .git entry, or '' when there is none.");
    m.def("git_head_sha", &git_head_sha, py::arg("gitdir"),
          "HEAD commit as the first 16 hex digits, parsed from a loose ref or "
          "packed-refs. 0 for a detached-then-deleted or unborn branch.");
    m.def("git_index_mtime", &git_index_mtime, py::arg("gitdir"),
          "mtime of .git/index in file_clock ticks. The half of the git axis that "
          "catches `git add`, which moves the index without moving HEAD.");

    // --- LRU --------------------------------------------------------------
    py::class_<LruCache>(m, "LruCache",
                         "Byte-capacity LRU eviction policy. Generic: it knows nothing "
                         "about files, git or tools. Use ToolCache for tool results.")
        .def(py::init<std::size_t>(), py::arg("capacity_bytes") = LruCache::kDefaultCapacityBytes,
             "Bound is BYTES, not entries. Tool results range from 40 bytes to megabytes; "
             "an entry count would let RSS track count instead of memory.")
        .def("get", &lru_get, py::arg("key"),
             "Fetch by key. Returns bytes, or None on a miss. A hit promotes the entry.")
        .def("put", &lru_put, py::arg("key"), py::arg("value"),
             "Store bytes. Returns False if the value exceeds the entire budget, in which "
             "case any previous copy of that key is dropped.")
        .def("contains", &lru_contains, py::arg("key"),
             "Membership test that does NOT change the recency order. Diagnostic only.")
        .def("erase", [](LruCache& self, std::uint64_t key) { return self.erase(key); },
             py::arg("key"), "Drop one entry. Returns whether it was resident.")
        .def("clear", &LruCache::clear, "Drop everything.")
        .def("set_capacity_bytes", &LruCache::set_capacity_bytes, py::arg("n"),
             "Resize the budget. Lowering it evicts immediately; 0 disables caching.")
        .def_property_readonly("capacity_bytes", &LruCache::capacity_bytes)
        .def_property_readonly("hit_ratio", &LruCache::hit_ratio,
                               "hits/(hits+misses), or 1.0 before any lookup.")
        .def("stats", [](const LruCache& self) { return lru_stats_to_dict(self.stats()); },
             "Hit/miss/eviction counters for the status bar.");

    // --- tool cache --------------------------------------------------------
    py::class_<CacheDescriptor>(m, "CacheDescriptor",
                                "The five key axes. canonical_args must already be "
                                "canonicalised by the caller — only the tool knows what its "
                                "arguments mean.")
        .def(py::init<>())
        .def_readwrite("tool", &CacheDescriptor::tool, "Axis 1: tool name.")
        .def_readwrite("schema_version", &CacheDescriptor::schema_version,
                       "Axis 2: bump when a tool's output format changes.")
        // canonical_args stays a plain std::string: it is only ever hashed, never
// handed back to the filesystem. pybind11 already casts py::str to std::string
// as UTF-8, so a Korean grep pattern hashes to the same bytes it does here.
// Only canonical_path needs the explicit u8string treatment, because C++
// reconstructs an fs::path from it.
        .def_readwrite("canonical_args", &CacheDescriptor::canonical_args,
                       "Axis 3: canonicalised argument blob.")
        .def_property(
            "canonical_path",
            [](const CacheDescriptor& d) { return u8_to_py(d.canonical_path); },
            [](CacheDescriptor& d, const py::object& v) { d.canonical_path = py_to_u8(v); },
            "Axis 5 subject: the file whose (mtime, size) forms the axis. Empty means "
            "'no file axis' — use for tools whose output does not depend on one file.")
        .def_readwrite("use_git_axis", &CacheDescriptor::use_git_axis,
                       "Axis 4 toggle. Turn off when the result cannot depend on the "
                       "working tree, to skip the probe entirely.");

    py::class_<ToolCache>(m, "ToolCache",
                          "Five-axis tool result cache. Git-aware invalidation: a file that "
                          "has not changed is never recomputed, and one that changed a "
                          "millisecond ago is never served from cache. No TTL.")
        .def(py::init([](std::size_t capacity_bytes, const py::object& workspace_root) {
                 // Both conversions run before the object exists, so a bad
                 // argument raises instead of leaving a half-built cache.
                 return std::make_unique<ToolCache>(capacity_bytes, py_to_path(workspace_root));
             }),
             py::arg("capacity_bytes") = LruCache::kDefaultCapacityBytes,
             py::arg("workspace_root") = ".",
             "Every READ tool is confined to the workspace, so the repository is a property "
             "of the workspace and is resolved once here rather than on every lookup.")
        .def("get", &tool_cache_get, py::arg("descriptor"),
             "Look up by descriptor. Returns bytes on a hit, None on a miss.")
        .def("put", &tool_cache_put, py::arg("descriptor"), py::arg("value"),
             "Store bytes. False means 'too large to cache' — not an error; carry on.")
        .def("key_for", &tool_cache_key_for, py::arg("descriptor"),
             "The key this descriptor currently maps to. For tests and 'why did this miss' "
             "diagnostics.")
        .def("invalidate_path", &tool_cache_invalidate_path, py::arg("path"),
             "Drop every entry for one path, at any axis-5 value. The WRITE-side call.")
        .def("clear", &ToolCache::clear,
             "Drop everything. Git axes need no explicit clearing: they are part of the key, "
             "so a commit makes the old entries unreachable on its own.")
        .def("canonical", &tool_cache_canonical, py::arg("path"),
             "Canonicalise a path for use as descriptor.canonical_path.")
        .def("revision", &tool_cache_revision,
             "Live working-tree state. Probed, not stored.")
        .def("stats", &tool_cache_stats, "Cache counters plus the current git state.")
        .def_property_readonly("capacity_bytes", &ToolCache::capacity_bytes)
        .def("set_capacity_bytes", &ToolCache::set_capacity_bytes, py::arg("n"),
             "Resize the byte budget.");
}