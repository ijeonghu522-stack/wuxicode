# ADR 0001 — Where the SSE boundary sits

- Status: accepted
- Date: 2026-06
- Applies to: `cpp/core/net/sse_parser.*`, `python/wx/llm/openai_client.py`

## Context

Streaming responses arrive as an arbitrary sequence of byte chunks. The cut
points are chosen by the network and the HTTP stack, not by us. Two failure
modes dominated implementation time when this was prototyped:

1. Splitting on `"\n"` in Python corrupts any multi-byte UTF-8 character that
   straddles a chunk boundary. Every streaming client gets this eventually.
2. Field-header state (`data:` vs `data`, trailing CR, multi-line payloads)
   re-implemented per provider drifts, and each drift is a separate bug.

## Decision

**C++ (`wx::core::net::SseParser`) owns framing. It owns nothing else.**

- Input: `std::string_view` of raw bytes.
- Output: `std::vector<SseEvent>{ event, data, id, retry }`.
- Line terminators are CR, LF, and CRLF. `cr_seen_` persists across `feed()`
  calls, so a CRLF split across a chunk boundary is handled correctly.
- Multiple `data:` fields join with a single `\n`; the trailing one is
  stripped at dispatch.
- `:` comment lines are counted and discarded (keepalive).
- Unknown fields are counted, never fatal.
- `id` persists across events, per the SSE specification.
- `finish()` flushes a tail event when a stream is cut mid-frame.

**Python owns everything payload-shaped:**

- `[DONE]` detection. The parser emits it as ordinary data; the client sees it
  and returns. Bytes after the sentinel are ignored.
- All JSON parsing.
- Tool-call fragment assembly, keyed by the provider's `index` field.
- Usage extraction, stop-reason extraction, retry policy.

## Why the parser is UTF-8 safe without doing anything about UTF-8

Line terminators are 0x0A and 0x0D. In UTF-8, continuation bytes are in
`[0x80..0xBF]` and lead bytes are in `[0xC2..0xF4]`. Neither range contains
0x0A or 0x0D. Therefore a code point can never be split by a line split, and
byte-level line splitting preserves UTF-8 validity by construction.

There is no carry buffer, no decoder state machine, and no replacement
character. The correct implementation is the one that cannot be wrong.

Verified in `sse_check.cpp`: a Korean payload cut in the middle of a
code point reassembles byte-identically, and all 63 single-cut offsets of a
mixed stream produce an identical event sequence.

## Consequences

**Accepted costs**

- One more language in the build. Mitigated: the extension is built by pip,
  the user never invokes a compiler.
- The parser must be re-implemented if the transport stops being SSE. Accepted:
  the transport will not stop being SSE, and the alternative — a hand-written
  `"\n\n"` splitter in Python — is where the bugs live.
- `parser.finish()` must be called explicitly at end of stream, or a tail event
  is lost. Encapsulated in `iter_sse_frames`; callers outside that function
  must remember.

**Rejected alternatives**

| Option | Why not |
|---|---|
| Pure-Python SSE parsing | The chunk-boundary bugs are the whole problem, and Python is where they get written fastest and hardest to test |
| Third-party SSE library | Same boundary problem, plus a dependency, plus no visibility when it is wrong |
| Push JSON parsing into C++ | C++ would then know the OpenAI schema. The next provider arrives with a different one, and C++ gets reopened. Schema churn is a Python problem |
| C++ aware of `[DONE]` | `[DONE]` is an OpenAI convention, not SSE. Same objection as above |
| Line-buffered C++ (consume whole lines) | Would need the caller to do correct splitting, which is the decision we are trying to centralize |

## Verification

Exit criteria for Phase 1A, all verified by `sse_check.cpp`:

1. UTF-8 code point cut mid-sequence -> byte-identical payload
2. `data:` field header cut mid-name -> correct value
3. Multi-line `data:` -> joined with `\n`, no trailing newline
4. `: keepalive` comments -> no event, counted
5. `[DONE]` -> surfaced as data, trailing events still framed
6. CRLF, lone CR, and CRLF split across chunks -> exactly one dispatch
7. All 63 single-cut offsets of a mixed stream -> identical event order
8. Repeated identical input -> identical output (no hidden nondeterminism)