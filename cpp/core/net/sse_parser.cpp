#include "core/net/sse_parser.hpp"

#include <utility>

namespace wx::core::net {
namespace {

constexpr std::string_view kFieldData = "data";
constexpr std::string_view kFieldEvent = "event";
constexpr std::string_view kFieldId = "id";
constexpr std::string_view kFieldRetry = "retry";

}  // namespace

// ---------------------------------------------------------------------------
// byte ingestion
// ---------------------------------------------------------------------------

void SseParser::push_byte(char c) {
    // A CR terminates the line immediately. We remember that we saw it, because
    // the next byte decides between CRLF (one terminator, swallow the LF) and
    // a lone CR followed by real content. cr_seen_ lives on the object, so this
    // works even when CR is the last byte of one chunk and LF the first of the
    // next.
    if (cr_seen_) {
        cr_seen_ = false;
        if (c == '\n') {
            return;  // second half of CRLF, carries no content
        }
    }

    if (c == '\r') {
        cr_seen_ = true;
        end_line();
        return;
    }

    if (c == '\n') {
        end_line();
        return;
    }

    line_.push_back(c);
}

void SseParser::feed(std::string_view chunk) {
    bytes_consumed_ += chunk.size();
    for (const char c : chunk) {
        push_byte(c);
    }
}

std::vector<SseEvent> SseParser::feed_and_take(std::string_view chunk) {
    feed(chunk);
    return take();
}

// ---------------------------------------------------------------------------
// line handling
// ---------------------------------------------------------------------------

void SseParser::end_line() {
    if (line_.empty()) {
        // Blank line is the event delimiter.
        dispatch();
    } else {
        handle_field(line_);
    }
    // clear(), not move: this is the buffer worth keeping warm.
    line_.clear();
}

void SseParser::handle_field(std::string_view line) {
    // ':' prefix is a comment. Streaming APIs use it as a keepalive so idle
    // connections are not reaped by intermediaries.
    if (line.front() == ':') {
        ++comments_;
        return;
    }

    std::string_view field;
    std::string_view value;

    const auto colon = line.find(':');
    if (colon == std::string_view::npos) {
        // Field with no colon: name is the whole line, value is empty.
        field = line;
        value = std::string_view{};
    } else {
        field = line.substr(0, colon);
        value = line.substr(colon + 1);
        // Spec strips exactly one leading space, not all whitespace.
        if (!value.empty() && value.front() == ' ') {
            value.remove_prefix(1);
        }
    }

    if (field == kFieldData) {
        // Every data field appends a '\n'; dispatch strips the final one, which
        // is how multi-line data rejoins with a single separator.
        data_.append(value);
        data_.push_back('\n');
    } else if (field == kFieldEvent) {
        event_.assign(value);
        has_event_ = true;
    } else if (field == kFieldId) {
        // Spec: an id field containing NUL must be ignored.
        if (value.find('\0') == std::string_view::npos) {
            last_id_.assign(value);
        }
    } else if (field == kFieldRetry) {
        retry_.assign(value);
        has_retry_ = true;
    } else {
        // Forward-compatible: unknown fields are counted, never fatal.
        ++unknown_fields_;
    }
}

void SseParser::dispatch() {
    if (data_.empty()) {
        // Spec: an empty data buffer produces no event. Only per-event state is
        // cleared — last_id_ deliberately survives, it is the reconnection id.
        event_.clear();
        has_event_ = false;
        retry_.clear();
        has_retry_ = false;
        return;
    }

    SseEvent ev;
    ev.data = std::move(data_);
    data_.clear();

    // Strip the newline the last data field appended.
    if (!ev.data.empty() && ev.data.back() == '\n') {
        ev.data.pop_back();
    }

    ev.event = has_event_ ? std::move(event_) : std::string("message");
    ev.id = last_id_;
    ev.retry = has_retry_ ? std::move(retry_) : std::string();
    ev.has_retry = has_retry_;

    event_.clear();
    has_event_ = false;
    retry_.clear();
    has_retry_ = false;

    out_.push_back(std::move(ev));
    ++events_emitted_;
}

// ---------------------------------------------------------------------------
// lifecycle
// ---------------------------------------------------------------------------

void SseParser::finish() {
    cr_seen_ = false;
    // A stream cut off mid-line still carries a complete field. Treat EOF as an
    // implicit line terminator so the tail event is not lost.
    if (!line_.empty()) {
        end_line();
    }
    dispatch();
}

std::vector<SseEvent> SseParser::take() {
    std::vector<SseEvent> result;
    result.swap(out_);
    return result;
}

void SseParser::reset() {
    line_.clear();
    data_.clear();
    event_.clear();
    last_id_.clear();
    retry_.clear();
    out_.clear();

    cr_seen_ = false;
    has_event_ = false;
    has_retry_ = false;

    events_emitted_ = 0;
    comments_ = 0;
    unknown_fields_ = 0;
    bytes_consumed_ = 0;
}

}  // namespace wx::core::net