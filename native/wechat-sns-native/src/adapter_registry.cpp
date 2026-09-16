#include "internal.hpp"

#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <regex>
#include <sstream>

#if defined(__APPLE__)
#include <dlfcn.h>
#elif defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace wcs {
namespace {

std::optional<std::string> json_string(const std::string &line, const std::string &key) {
    const std::regex expression(
        "\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    std::smatch match;
    if (!std::regex_search(line, match, expression) || match.size() != 2) {
        return std::nullopt;
    }
    return match[1].str();
}

bool is_sha256(const std::string &value) {
    if (value == "*") {
        return true;
    }
    if (value.size() != 64) {
        return false;
    }
    for (char character : value) {
        if (!std::isxdigit(static_cast<unsigned char>(character))) {
            return false;
        }
    }
    return true;
}

}  // namespace

std::string default_adapter_manifest_path() {
    const char *explicit_path = std::getenv("WECHAT_SNS_NATIVE_ADAPTERS");
    if (explicit_path != nullptr && explicit_path[0] != '\0') {
        return explicit_path;
    }
#if defined(__APPLE__)
    Dl_info info{};
    if (dladdr(reinterpret_cast<const void *>(&default_adapter_manifest_path), &info) != 0 &&
        info.dli_fname != nullptr) {
        return (std::filesystem::path(info.dli_fname).parent_path() /
                "wechat_sns_adapters.jsonl")
            .string();
    }
#elif defined(_WIN32)
    HMODULE module = nullptr;
    if (GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&default_adapter_manifest_path),
            &module)) {
        std::vector<wchar_t> buffer(32768);
        const DWORD length = GetModuleFileNameW(
            module, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length > 0 && length < buffer.size()) {
            return (std::filesystem::path(std::wstring(buffer.data(), length)).parent_path() /
                    L"wechat_sns_adapters.jsonl")
                .u8string();
        }
    }
#endif
    return "wechat_sns_adapters.jsonl";
}

std::vector<Adapter> load_adapters(const std::string &path, std::string *error) {
    std::vector<Adapter> adapters;
    std::ifstream input(path);
    if (!input) {
        if (error != nullptr) {
            *error = "adapter manifest is not readable: " + path;
        }
        return adapters;
    }
    std::string line;
    size_t line_number = 0;
    while (std::getline(input, line)) {
        ++line_number;
        if (line.empty() || line[0] == '#') {
            continue;
        }
        Adapter adapter;
        const auto id = json_string(line, "adapterId");
        const auto platform = json_string(line, "platform");
        const auto architecture = json_string(line, "architecture");
        const auto version = json_string(line, "wechatVersion");
        const auto hash = json_string(line, "binarySha256");
        const auto status = json_string(line, "status");
        if (!id || !platform || !architecture || !version || !hash || !status ||
            id->empty() || platform->empty() || architecture->empty() || version->empty() ||
            !is_sha256(*hash)) {
            if (error != nullptr) {
                *error = "invalid adapter manifest record at line " +
                    std::to_string(line_number);
            }
            adapters.clear();
            return adapters;
        }
        adapter.adapter_id = *id;
        adapter.platform = *platform;
        adapter.architecture = *architecture;
        adapter.wechat_version = *version;
        adapter.binary_sha256 = *hash;
        if (*status == "verified") {
            adapter.status = AdapterStatus::Verified;
        } else if (*status == "candidate") {
            adapter.status = AdapterStatus::Candidate;
        } else if (*status == "research_required") {
            adapter.status = AdapterStatus::ResearchRequired;
        } else {
            if (error != nullptr) {
                *error = "unknown adapter status at line " + std::to_string(line_number);
            }
            adapters.clear();
            return adapters;
        }
        adapter.request_entry_pattern = json_string(line, "requestEntryPattern").value_or("");
        adapter.response_cursor_pattern =
            json_string(line, "responseCursorPattern").value_or("");
        adapter.database_write_pattern =
            json_string(line, "databaseWritePattern").value_or("");
        adapters.push_back(std::move(adapter));
    }
    return adapters;
}

const Adapter *select_adapter(
    const std::vector<Adapter> &adapters,
    const ProcessInfo &process,
    bool allow_candidate) {
    const Adapter *wildcard = nullptr;
    for (const Adapter &adapter : adapters) {
        if (adapter.platform != process.platform ||
            adapter.architecture != process.architecture) {
            continue;
        }
        const bool version_matches = adapter.wechat_version == process.version;
        const bool hash_matches = adapter.binary_sha256 == process.binary_sha256;
        if (version_matches && hash_matches) {
            if (adapter.status == AdapterStatus::Candidate && !allow_candidate) {
                continue;
            }
            return &adapter;
        }
        if (allow_candidate && adapter.status == AdapterStatus::Candidate &&
            adapter.wechat_version == "*" && adapter.binary_sha256 == "*") {
            wildcard = &adapter;
        }
    }
    return wildcard;
}

bool validate_adapter_binary(
    const Adapter &adapter,
    const std::string &binary_path,
    std::string *error) {
    if (adapter.status == AdapterStatus::ResearchRequired) {
        if (error != nullptr) {
            *error = "adapter has not completed structural validation";
        }
        return false;
    }
    const std::string patterns[] = {
        adapter.request_entry_pattern,
        adapter.response_cursor_pattern,
        adapter.database_write_pattern,
    };
    std::ifstream input(binary_path, std::ios::binary);
    if (!input) {
        if (error != nullptr) {
            *error = "WeChat executable is not readable";
        }
        return false;
    }
    std::vector<uint8_t> bytes(
        (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    if (bytes.empty()) {
        if (error != nullptr) {
            *error = "WeChat executable is empty";
        }
        return false;
    }
    for (const std::string &pattern_text : patterns) {
        std::vector<PatternByte> pattern;
        if (!parse_pattern(pattern_text, &pattern, error)) {
            return false;
        }
        const PatternMatch match = find_unique_pattern(bytes.data(), bytes.size(), pattern);
        if (!match.offset.has_value() || match.match_count != 1) {
            if (error != nullptr) {
                *error = "an adapter signature did not resolve to exactly one location";
            }
            return false;
        }
    }
    return true;
}

}  // namespace wcs
