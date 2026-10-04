#include "core/text/tokenizer_heuristic.hpp"

#include <cmath>
#include <cstdint>

namespace wx::core::text {
namespace {

enum class CharClass : std::uint8_t {
    Space,
    Hex,
    Base64,
    Ascii,
    Cjk,
    Other,
};

inline bool is_space_cp(char32_t cp) noexcept {
    return cp == ' ' || cp == '\t' || cp == '\n' || cp == '\r' || cp == '\f' || cp == '\v';
}

inline bool is_hex_cp(char32_t cp) noexcept {
    return (cp >= '0' && cp <= '9') || (cp >= 'a' && cp <= 'f') || (cp >= 'A' && cp <= 'F');
}

inline bool is_b64_cp(char32_t cp) noexcept {
    return (cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z') || (cp >= '0' && cp <= '9') ||
           cp == '+' || cp == '/' || cp == '=';
}

// Hangul jamo, kana, CJK unified + extensions, Hangul syllables, fullwidth.
// Korean syllables (U+AC00..U+D7AF) are the case that matters here: 3 UTF-8
// bytes, ~1 token.
inline bool is_cjk_cp(char32_t cp) noexcept {
    return (cp >= 0x1100 && cp <= 0x11FF) || (cp >= 0x3040 && cp <= 0x30FF) ||
           (cp >= 0x3130 && cp <= 0x318F) || (cp >= 0x3400 && cp <= 0x4DBF) ||
           (cp >= 0x4E00 && cp <= 0x9FFF) || (cp >= 0xA960 && cp <= 0xA97F) ||
           (cp >= 0xAC00 && cp <= 0xD7AF) || (cp >= 0xF900 && cp <= 0xFAFF) ||
           (cp >= 0xFF00 && cp <= 0xFFEF);
}

// Malformed input decodes to U+FFFD with length 1 and is bucketed as Other.
// A truncated trailing byte must not read past the end of the view, and must
// not abort the count.
inline bool decode_utf8(std::string_view s, std::size_t i, char32_t& cp,
                        std::size_t& len) noexcept {
    const auto b0 = static_cast<unsigned char>(s[i]);
    if (b0 < 0x80) {
        cp = b0;
        len = 1;
        return true;
    }

    std::size_t need = 0;
    char32_t v = 0;
    if ((b0 & 0xE0) == 0xC0) {
        need = 2;
        v = static_cast<char32_t>(b0 & 0x1Fu);
    } else if ((b0 & 0xF0) == 0xE0) {
        need = 3;
        v = static_cast<char32_t>(b0 & 0x0Fu);
    } else if ((b0 & 0xF8) == 0xF0) {
        need = 4;
        v = static_cast<char32_t>(b0 & 0x07u);
    } else {
        cp = 0xFFFD;
        len = 1;
        return false;
    }

    if (i + need > s.size()) {
        cp = 0xFFFD;
        len = 1;
        return false;
    }

    for (std::size_t k = 1; k < need; ++k) {
        const auto bk = static_cast<unsigned char>(s[i + k]);
        if ((bk & 0xC0) != 0x80) {
            cp = 0xFFFD;
            len = 1;
            return false;
        }
        v = (v << 6) | static_cast<char32_t>(bk & 0x3Fu);
    }

    cp = v;
    len = need;
    return true;
}

// Hex is tested before base64 so a digest is classified as hex, not as a
// generic alphanumeric run.
inline CharClass classify(char32_t cp) noexcept {
    if (cp < 0x80) {
        if (is_space_cp(cp)) return CharClass::Space;
        if (is_hex_cp(cp)) return CharClass::Hex;
        if (is_b64_cp(cp)) return CharClass::Base64;
        return CharClass::Ascii;
    }
    if (is_cjk_cp(cp)) return CharClass::Cjk;
    return CharClass::Other;
}

struct Buckets {
    double ascii = 0.0;  // plain ascii, plus hex/base64 runs below threshold
    double whitespace = 0.0;
    double cjk = 0.0;
    double other = 0.0;
    double hex_dense = 0.0;  // long hex run: near-random, cheap per char
    double b64_dense = 0.0;  // long base64 run
};

}  // namespace

double estimate(std::string_view text, const HeuristicCalibration& cal) noexcept {
    if (text.empty()) {
        return 0.0;
    }

    Buckets b{};
    std::size_t i = 0;

    CharClass run_class = CharClass::Other;
    std::size_t run_len = 0;
    bool in_run = false;

    while (i < text.size()) {
        char32_t cp = 0;
        std::size_t len = 1;
        decode_utf8(text, i, cp, len);
        i += len;

        const CharClass cls = classify(cp);

        if (in_run && cls == run_class) {
            ++run_len;
            continue;
        }

        // close the previous run
        if (in_run) {
            switch (run_class) {
                case CharClass::Space:
                    b.whitespace += static_cast<double>(run_len);
                    break;
                case CharClass::Hex:
                    // A short hex-looking run is an identifier, not a digest.
                    if (run_len >= cal.hex_run_min) {
                        b.hex_dense += static_cast<double>(run_len);
                    } else {
                        b.ascii += static_cast<double>(run_len);
                    }
                    break;
                case CharClass::Base64:
                    // A 64+ char alphanumeric run is essentially always a blob;
                    // ordinary words and identifiers are far shorter.
                    if (run_len >= cal.base64_run_min) {
                        b.b64_dense += static_cast<double>(run_len);
                    } else {
                        b.ascii += static_cast<double>(run_len);
                    }
                    break;
                case CharClass::Ascii:
                    b.ascii += static_cast<double>(run_len);
                    break;
                case CharClass::Cjk:
                    b.cjk += static_cast<double>(run_len);
                    break;
                case CharClass::Other:
                    b.other += static_cast<double>(run_len);
                    break;
            }
        }

        in_run = true;
        run_class = cls;
        run_len = 1;
    }

    // flush the trailing run
    if (in_run) {
        switch (run_class) {
            case CharClass::Space:
                b.whitespace += static_cast<double>(run_len);
                break;
            case CharClass::Hex:
                if (run_len >= cal.hex_run_min) {
                    b.hex_dense += static_cast<double>(run_len);
                } else {
                    b.ascii += static_cast<double>(run_len);
                }
                break;
            case CharClass::Base64:
                if (run_len >= cal.base64_run_min) {
                    b.b64_dense += static_cast<double>(run_len);
                } else {
                    b.ascii += static_cast<double>(run_len);
                }
                break;
            case CharClass::Ascii:
                b.ascii += static_cast<double>(run_len);
                break;
            case CharClass::Cjk:
                b.cjk += static_cast<double>(run_len);
                break;
            case CharClass::Other:
                b.other += static_cast<double>(run_len);
                break;
        }
    }

    const double tokens =
        b.ascii / cal.ascii_chars_per_token +
        b.whitespace / cal.whitespace_chars_per_token +
        b.cjk / cal.cjk_chars_per_token +
        b.other / cal.other_chars_per_token +
        b.hex_dense / cal.hex_chars_per_token +
        b.b64_dense / cal.base64_chars_per_token;

    return tokens > 0.0 ? tokens : 0.0;
}

std::uint64_t count(std::string_view text, const HeuristicCalibration& cal) noexcept {
    if (text.empty()) {
        return 0;
    }
    const double t = estimate(text, cal);
    if (t <= 0.0) {
        return 1;  // any non-empty input costs at least one token
    }
    return static_cast<std::uint64_t>(std::ceil(t));
}

const char* mode_name(TokenizerMode mode) noexcept {
    switch (mode) {
        case TokenizerMode::Exact:
            return "exact";
        case TokenizerMode::Heuristic:
        default:
            return "heuristic";
    }
}

}  // namespace wx::core::text