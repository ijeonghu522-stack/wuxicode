// WUXICODE — SSE framing.
//
// SCOPE (see docs/adr/0001-sse-boundary.md):
// This parser turns an arbitrary byte stream into complete SSE events. It does
// NOT know what an event's payload means. No JSON parsing, no OpenAI schema
// knowledge, no `[DONE]` handling — those are Python's job, one layer up.
//
// WHY IT IS UTF-8 SAFE WITHOUT DOING ANYTHING ABOUT IT:
// Line terminators are CR (0x0D) and LF (0x0A). Both are ASCII. In UTF-8 a
// multi-byte sequence is a lead byte in [C2..F4] followed by continuation bytes
// in [80..BF] — none of which can be 0x0A or 0x0D. Therefore a code point can
// never be split by a line split, and splitting on CR/LF preserves UTF-8
// validity by construction. A Korean character arriving across two HTTP chunks
// reassembles correctly with no carry buffer and no special case.
//
// The failure this design eliminates: a naive parser that finds "\n" and takes
// everything before it will corrupt any multi-byte character straddling a chunk
// boundary, which in practice is every streaming response.

#pragma once

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace wx::core::net {

struct SseEvent {
    std::string event = "message";  // "message" when the stream omits `event:`
    std::string data;               // multiple `data:` lines joined with '\n'
    std::string id;                 // last event id seen, per SSE spec persistence
    std::string retry;              // raw `retry:` payload; consumer parses it
    bool has_retry = false;
};

class SseParser {
public:
    SseParser() = default;

    // Consume a chunk of bytes in stream order and append to the event queue.
    // Chunks may split anywhere: mid-line, between CR and LF, mid-codepoint.
    void feed(std::string_view chunk);

    // Feed + drain in one call. This is the only entry point Python uses.
    [[nodiscard]] std::vector<SseEvent> feed_and_take(std::string_view chunk);

    // Empty line terminates an event per spec: emit queued data, then clear.
    // Exposed so a caller can force a flush on connection teardown.
    void finish();

    [[nodiscard]] std::vector<SseEvent> take();

    void reset();

    // --- observability ---------------------------------------------------
    // Needed for the status bar and for diagnosing a stuck stream.

    [[nodiscard]] std::size_t buffered() const noexcept { return line_.size(); }
    [[nodiscard]] bool trailing_cr() const noexcept { return cr_seen_; }
    [[nodiscard]] std::size_t events_emitted() const noexcept { return events_emitted_; }
    [[nodiscard]] std::size_t comments_seen() const noexcept { return comments_; }
    [[nodiscard]] std::size_t unknown_fields() const noexcept { return unknown_fields_; }
    [[nodiscard]] std::size_t bytes_consumed() const noexcept { return bytes_consumed_; }
    [[nodiscard]] const std::string& last_id() const noexcept { return last_id_; }

private:
    void push_byte(char c);
    void end_line();
    void handle_field(std::string_view line);
    void dispatch();

    // line_ and data_ are cleared, never moved out of, so their capacity is
    // retained across events — that is the allocation that actually matters.
    // Event payloads move out, costing one small heap block per event
    // (~50-400 bytes at ~50 events/sec). Buffer recycling for that is not
    // worth the complexity.
    std::string line_;     // partial line, no terminator
    std::string data_;     // accumulated `data:` payloads, each '\n'-terminated
    std::string event_;
    std::string last_id_;  // persists across events (spec)
    std::string retry_;

    std::vector<SseEvent> out_;

    bool cr_seen_ = false;
    bool has_event_ = false;
    bool has_retry_ = false;

    std::size_t events_emitted_ = 0;
    std::size_t comments_ = 0;
    std::size_t unknown_fields_ = 0;
    std::size_t bytes_consumed_ = 0;
};

}  // namespace wx::core::net