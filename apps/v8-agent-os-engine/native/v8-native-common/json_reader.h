#pragma once
#include "windows.h"
#include <cstring>
#include <limits>
namespace v8native {
class JsonReader {
    const std::string &input_;
    size_t at_ = 0;
    size_t limit_;

  public:
    explicit JsonReader(const std::string &input, size_t limit = 16384)
        : input_(input), limit_(limit) {}
    void space() {
        while (at_ < input_.size() && (input_[at_] == ' ' || input_[at_] == '\r' ||
                                       input_[at_] == '\n' || input_[at_] == '\t'))
            ++at_;
    }
    bool take(char c) {
        space();
        if (at_ >= input_.size() || input_[at_] != c)
            return false;
        ++at_;
        return true;
    }
    bool end() {
        space();
        return at_ == input_.size();
    }
    bool integer(uint64_t &value) {
        space();
        size_t start = at_;
        value = 0;
        while (at_ < input_.size() && input_[at_] >= '0' && input_[at_] <= '9') {
            unsigned digit = input_[at_++] - '0';
            if (value > (std::numeric_limits<uint64_t>::max() - digit) / 10)
                return false;
            value = value * 10 + digit;
        }
        return at_ != start && !(at_ - start > 1 && input_[start] == '0');
    }
    bool text(std::wstring &result) {
        if (!take('"'))
            return false;
        std::string bytes;
        bytes.reserve(limit_); // no reallocations leaving old secret fragments behind
        struct Wipe {
            std::string &s;
            ~Wipe() {
                if (!s.empty())
                    SecureZeroMemory(s.data(), s.size());
            }
        } wipe{bytes};
        while (at_ < input_.size()) {
            unsigned char c = static_cast<unsigned char>(input_[at_++]);
            if (c == '"') {
                int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                                                static_cast<int>(bytes.size()), nullptr, 0);
                if (!bytes.empty() && !count)
                    return false;
                result.resize(count);
                if (count)
                    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                                        static_cast<int>(bytes.size()), result.data(), count);
                return result.find(L'\0') == std::wstring::npos;
            }
            if (c < 0x20)
                return false;
            if (c != '\\') {
                bytes.push_back(static_cast<char>(c));
                continue;
            }
            if (at_ == input_.size())
                return false;
            c = static_cast<unsigned char>(input_[at_++]);
            const char *escapes = "\"\\/bfnrt";
            const char *values = "\"\\/\b\f\n\r\t";
            const char *found = strchr(escapes, c);
            if (found) {
                bytes.push_back(values[found - escapes]);
                continue;
            }
            if (c != 'u' || at_ + 4 > input_.size())
                return false;
            auto hex = [&](uint32_t &cp) {
                cp = 0;
                for (unsigned i = 0; i < 4; ++i) {
                    if (at_ == input_.size())
                        return false;
                    char digit = input_[at_++];
                    int n = digit >= '0' && digit <= '9'   ? digit - '0'
                            : digit >= 'a' && digit <= 'f' ? digit - 'a' + 10
                            : digit >= 'A' && digit <= 'F' ? digit - 'A' + 10
                                                           : -1;
                    if (n < 0)
                        return false;
                    cp = cp * 16 + n;
                }
                return true;
            };
            uint32_t cp = 0;
            if (!hex(cp) || !cp)
                return false;
            if (cp >= 0xd800 && cp <= 0xdbff) {
                if (at_ + 6 > input_.size() || input_[at_++] != '\\' || input_[at_++] != 'u')
                    return false;
                uint32_t low = 0;
                if (!hex(low) || low < 0xdc00 || low > 0xdfff)
                    return false;
                cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
            } else if (cp >= 0xdc00 && cp <= 0xdfff)
                return false;
            if (cp < 0x80)
                bytes.push_back(static_cast<char>(cp));
            else if (cp < 0x800) {
                bytes.push_back(static_cast<char>(0xc0 | (cp >> 6)));
                bytes.push_back(static_cast<char>(0x80 | (cp & 63)));
            } else if (cp < 0x10000) {
                bytes.push_back(static_cast<char>(0xe0 | (cp >> 12)));
                bytes.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
                bytes.push_back(static_cast<char>(0x80 | (cp & 63)));
            } else {
                bytes.push_back(static_cast<char>(0xf0 | (cp >> 18)));
                bytes.push_back(static_cast<char>(0x80 | ((cp >> 12) & 63)));
                bytes.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
                bytes.push_back(static_cast<char>(0x80 | (cp & 63)));
            }
        }
        return false;
    }
};
} // namespace v8native
