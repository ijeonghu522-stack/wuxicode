// WUXICODE — stable 64-bit hashing.
//
// Header-only, no allocation, no <random>, no std::hash. std::hash is
// randomized per process on MSVC, which would make cache keys change on every
// run. These values must be identical across runs, processes, platforms and
// compilers — they are cache-key material.
//
// Algorithm: FNV-1a over raw bytes, then a SplitMix64 finalizer. FNV alone has
// poor avalanche in the high bits, and cache keys get truncated to 16 hex chars
// in some places, so the finalizer matters.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace wx::core {

inline constexpr char kHexDigits[] = "0123456789abcdef";

inline constexpr std::uint64_t kFnvOffsetBasis = 0xcbf29ce484222325ULL;
inline constexpr std::uint64_t kFnvPrime = 0x00000100000001b3ULL;

// FNV-1a. Stable, order-sensitive, byte-exact.
[[nodiscard]] constexpr std::uint64_t fnv1a(std::string_view bytes,
                                            std::uint64_t seed = kFnvOffsetBasis) noexcept {
    std::uint64_t h = seed;
    for (const char ch : bytes) {
        h ^= static_cast<std::uint64_t>(static_cast<unsigned char>(ch));
        h *= kFnvPrime;
    }
    return h;
}

// SplitMix64 finalizer: full avalanche so any single input bit flips ~half the
// output bits. Turns FNV's weak high bits into usable key material.
[[nodiscard]] constexpr std::uint64_t mix64(std::uint64_t x) noexcept {
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
}

// Canonical hash for cache keys and content addressing.
[[nodiscard]] constexpr std::uint64_t hash64(std::string_view bytes) noexcept {
    return mix64(fnv1a(bytes));
}

// Order-independent combination, for building a key out of several fields
// where "which field goes first" should not change the result.
[[nodiscard]] constexpr std::uint64_t hash64_combine(std::uint64_t a,
                                                     std::uint64_t b) noexcept {
    return mix64(a ^ (b + 0x9e3779b97f4a7c15ULL + (a << 6) + (a >> 2)));
}

[[nodiscard]] constexpr std::uint64_t hash64_combine(std::string_view bytes,
                                                     std::uint64_t acc) noexcept {
    return hash64_combine(acc, hash64(bytes));
}

// Canonical cache key: 16 lowercase hex chars, zero-padded.
//
// Returns by value as a POD array — NOT a string_view. A string_view into a
// function-local buffer would dangle the instant this returned, and it cannot
// be constexpr either. Callers that need a string use to_hex16().
[[nodiscard]] constexpr std::array<char, 16> to_hex16_chars(std::uint64_t value) noexcept {
    std::array<char, 16> out{};
    for (std::size_t i = 16; i-- > 0;) {
        out[i] = kHexDigits[value & 0xFULL];
        value >>= 4;
    }
    return out;
}

// Same key, owning form for filenames, log lines and map keys.
// Short enough to read, wide enough that a collision costs one recompute and
// nothing else: a 160 MB cache holds well under 10^6 entries.
[[nodiscard]] inline std::string to_hex16(std::uint64_t value) {
    const auto chars = to_hex16_chars(value);
    return std::string(chars.data(), chars.size());
}

}  // namespace wx::core