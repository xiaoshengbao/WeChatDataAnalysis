#include "internal.hpp"

#include <cctype>
#include <sstream>

namespace wcs {

bool parse_pattern(
    const std::string &pattern,
    std::vector<PatternByte> *result,
    std::string *error) {
    if (result == nullptr) {
        return false;
    }
    result->clear();
    std::istringstream stream(pattern);
    std::string token;
    while (stream >> token) {
        if (token == "?" || token == "??") {
            result->push_back(PatternByte{0, true});
            continue;
        }
        if (token.size() != 2 || !std::isxdigit(static_cast<unsigned char>(token[0])) ||
            !std::isxdigit(static_cast<unsigned char>(token[1]))) {
            if (error != nullptr) {
                *error = "pattern contains a token that is not a byte or wildcard";
            }
            result->clear();
            return false;
        }
        unsigned int value = 0;
        std::istringstream hex(token);
        hex >> std::hex >> value;
        result->push_back(PatternByte{static_cast<uint8_t>(value), false});
    }
    if (result->empty()) {
        if (error != nullptr) {
            *error = "pattern is empty";
        }
        return false;
    }
    return true;
}

PatternMatch find_unique_pattern(
    const uint8_t *data,
    size_t data_size,
    const std::vector<PatternByte> &pattern) {
    PatternMatch result;
    if (data == nullptr || pattern.empty() || data_size < pattern.size()) {
        return result;
    }
    for (size_t offset = 0; offset <= data_size - pattern.size(); ++offset) {
        bool matches = true;
        for (size_t index = 0; index < pattern.size(); ++index) {
            if (!pattern[index].wildcard && data[offset + index] != pattern[index].value) {
                matches = false;
                break;
            }
        }
        if (!matches) {
            continue;
        }
        ++result.match_count;
        if (result.match_count == 1) {
            result.offset = offset;
        } else {
            result.offset.reset();
        }
    }
    return result;
}

}  // namespace wcs
