#ifndef WECHAT_SNS_NATIVE_INTERNAL_HPP
#define WECHAT_SNS_NATIVE_INTERNAL_HPP

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "wechat_sns_bridge_protocol.h"

namespace wcs {

constexpr int32_t kStatusOk = 0;
constexpr int32_t kStatusInvalidArgument = -1;
constexpr int32_t kStatusUnavailable = -2;
constexpr int32_t kStatusProtocol = -3;
constexpr int32_t kStatusIo = -4;
constexpr int32_t kStatusNotFound = -13;
constexpr int32_t kStatusBusy = -14;
constexpr int32_t kStatusUnsupported = -17;
constexpr int32_t kStatusTimeout = -18;

struct ProcessInfo {
    bool platform_supported = false;
    bool architecture_supported = false;
    uint32_t process_id = 0;
    std::string platform;
    std::string architecture;
    std::string version;
    std::string build;
    std::string executable_path;
    std::string binary_sha256;
};

enum class AdapterStatus {
    Verified,
    Candidate,
    ResearchRequired,
};

struct Adapter {
    std::string adapter_id;
    std::string platform;
    std::string architecture;
    std::string wechat_version;
    std::string binary_sha256;
    AdapterStatus status = AdapterStatus::ResearchRequired;
    std::string request_entry_pattern;
    std::string response_cursor_pattern;
    std::string database_write_pattern;
};

struct PatternByte {
    uint8_t value = 0;
    bool wildcard = false;
};

struct PatternMatch {
    std::optional<size_t> offset;
    size_t match_count = 0;
};

bool probe_wechat(ProcessInfo *result, std::string *error);
std::string default_adapter_manifest_path();
std::vector<Adapter> load_adapters(const std::string &path, std::string *error);
const Adapter *select_adapter(
    const std::vector<Adapter> &adapters,
    const ProcessInfo &process,
    bool allow_candidate);
bool validate_adapter_binary(
    const Adapter &adapter,
    const std::string &binary_path,
    std::string *error);
bool parse_pattern(
    const std::string &pattern,
    std::vector<PatternByte> *result,
    std::string *error);
PatternMatch find_unique_pattern(
    const uint8_t *data,
    size_t data_size,
    const std::vector<PatternByte> &pattern);

bool bridge_exchange(
    uint32_t process_id,
    uint16_t command,
    uint64_t operation_nonce,
    const void *request,
    uint32_t request_size,
    void *response,
    uint32_t response_size,
    int32_t *response_status,
    std::string *error);

template <size_t N>
bool copy_fixed(char (&destination)[N], const std::string &value) {
    if (value.size() >= N) {
        return false;
    }
    for (size_t index = 0; index < N; ++index) {
        destination[index] = 0;
    }
    for (size_t index = 0; index < value.size(); ++index) {
        destination[index] = value[index];
    }
    return true;
}

template <size_t N>
bool copy_fixed(char (&destination)[N], const char *value) {
    return value != nullptr && copy_fixed(destination, std::string(value));
}

}  // namespace wcs

#endif
