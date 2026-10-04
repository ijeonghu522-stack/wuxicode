// WUXICODE — pybind11 bindings for wxcore.
//
// One module, `_wx_core`. Everything Python touches goes through here.
//
// BOUNDARY RULE (docs/adr/0001-sse-boundary.md): this layer translates types
// and nothing else. It does not parse JSON, does not know about OpenAI, does
// not decide anything. Every function below is either a pure computation or a
// thin wrapper over a wxcore class.

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "core/base/hash.hpp"
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
}