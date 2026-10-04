// WUXICODE — heuristic token counter.
//
// PURPOSE: be fast, allocation-free and correct to within a few percent when
// tiktoken is unavailable. This is the fallback path, not the primary one.
// Phase 3 wires the exact tiktoken counter; until then the status bar shows
// "heuristic" so nobody budgets against a number that is not what it claims to
// be.
//
// WHY A HEURISTIC AND NOT chars/4:
// The agent's entire workload is source code. Code is not English prose.
//   - 8-space indentation is expensive in BPE, not cheap
//   - hex digests and base64 blobs are near-random and cost ~2 chars/token
//   - Korean is ~1 token per syllable, while UTF-8 makes it 3 bytes
// A flat chars/4 under-counts indented code by ~35% and over-counts Korean by
// ~3x. Both errors compound across a long session and both land directly on
// context-budget decisions, so the counter buckets by character class.
//
// NOT IN SCOPE: per-message protocol overhead (~3-4 tokens for role and
// framing). That is the assembler's number, not the text counter's.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace wx::core::text {

enum class TokenizerMode : int {
    Heuristic = 0,  // this module
    Exact = 1,      // tiktoken, Phase 3
};

// Tunable without touching the algorithm. Phase 3 calibrates these against a
// real corpus of tool results; that is why they are named constants in a struct
// rather than literals in the scan loop.
struct HeuristicCalibration {
    double ascii_chars_per_token = 4.0;
    double whitespace_chars_per_token = 2.5;
    double cjk_chars_per_token = 1.0;
    double other_chars_per_token = 2.0;
    double hex_chars_per_token = 2.0;
    double base64_chars_per_token = 3.0;
    std::size_t hex_run_min = 16;
    std::size_t base64_run_min = 64;
};

// Fractional token estimate. 0.0 for empty input.
[[nodiscard]] double estimate(
    std::string_view text,
    const HeuristicCalibration& cal = HeuristicCalibration{}) noexcept;

// Rounded-up estimate. Empty input is 0; any non-empty input is at least 1.
[[nodiscard]] std::uint64_t count(
    std::string_view text,
    const HeuristicCalibration& cal = HeuristicCalibration{}) noexcept;

// Exposed for the binding surface and for tests.
[[nodiscard]] const char* mode_name(TokenizerMode mode) noexcept;

}  // namespace wx::core::text