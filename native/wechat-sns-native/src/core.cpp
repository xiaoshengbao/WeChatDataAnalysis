#include "wechat_sns_native.h"

#include "internal.hpp"

#include <atomic>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>
#include <unordered_map>

namespace {

struct ActiveRequest {
    uint32_t process_id = 0;
    uint64_t remote_handle = 0;
    bool version_verified = false;
};

std::mutex g_requests_mutex;
std::unordered_map<uint64_t, ActiveRequest> g_requests;
std::atomic<uint64_t> g_next_handle{1};

bool valid_text(const char *value, size_t maximum_bytes, bool optional = false) {
    if (value == nullptr) {
        return optional;
    }
    const size_t length = strnlen(value, maximum_bytes + 1);
    return (optional || length > 0) && length <= maximum_bytes;
}

template <size_t N>
bool read_fixed(const char (&value)[N], std::string *result) {
    const size_t length = strnlen(value, N);
    if (length >= N) {
        return false;
    }
    for (size_t index = length + 1; index < N; ++index) {
        if (value[index] != 0) {
            return false;
        }
    }
    *result = std::string(value, length);
    return true;
}

void reset_capability(wce_wechat_moments_sync_capability *result) {
    std::memset(result, 0, sizeof(*result));
    result->struct_size = sizeof(*result);
}

void reset_poll(wce_wechat_moments_sync_poll_result *result) {
    std::memset(result, 0, sizeof(*result));
    result->struct_size = sizeof(*result);
}

bool decide_capability(
    const wce_wechat_moments_sync_capability_options *options,
    wce_wechat_moments_sync_capability *result) {
    reset_capability(result);
    wcs::ProcessInfo process;
    std::string probe_error;
    if (!wcs::probe_wechat(&process, &probe_error)) {
        result->platform_supported = process.platform_supported ? 1u : 0u;
        result->reason = WCE_MOMENTS_RUNTIME_UNAVAILABLE;
        return true;
    }
    result->platform_supported = process.platform_supported ? 1u : 0u;
    result->wechat_process_id = process.process_id;
    wcs::copy_fixed(result->actual_wechat_version, process.version);
    if (!process.platform_supported) {
        result->reason = WCE_MOMENTS_UNSUPPORTED_PLATFORM;
        return true;
    }
    if (!process.architecture_supported) {
        result->reason = WCE_MOMENTS_UNSUPPORTED_ARCHITECTURE;
        return true;
    }
    if (process.process_id == 0) {
        result->reason = WCE_MOMENTS_WECHAT_NOT_RUNNING;
        return true;
    }
    std::string manifest_error;
    const std::vector<wcs::Adapter> adapters =
        wcs::load_adapters(wcs::default_adapter_manifest_path(), &manifest_error);
    if (adapters.empty()) {
        result->reason = WCE_MOMENTS_RUNTIME_UNAVAILABLE;
        return true;
    }
    const bool allow_candidate = (options->flags & 1u) != 0;
    const wcs::Adapter *adapter =
        wcs::select_adapter(adapters, process, allow_candidate);
    if (adapter == nullptr) {
        result->reason = WCE_MOMENTS_WECHAT_VERSION_UNVERIFIED;
        return true;
    }
    wcs::copy_fixed(result->adapter_id, adapter->adapter_id);
    if (adapter->status == wcs::AdapterStatus::ResearchRequired) {
        result->reason = WCE_MOMENTS_WECHAT_VERSION_UNVERIFIED;
        return true;
    }
    std::string validation_error;
    if (!wcs::validate_adapter_binary(*adapter, process.executable_path, &validation_error)) {
        result->reason = WCE_MOMENTS_HOOK_VALIDATION_FAILED;
        return true;
    }

    wcs_bridge_capability_request bridge_request{};
    bridge_request.wechat_process_id = process.process_id;
    bridge_request.flags = options->flags;
    if (!wcs::copy_fixed(bridge_request.account, options->account_utf8) ||
        !wcs::copy_fixed(bridge_request.binary_sha256, process.binary_sha256) ||
        !wcs::copy_fixed(bridge_request.adapter_id, adapter->adapter_id)) {
        result->reason = WCE_MOMENTS_INTERNAL;
        return true;
    }
    wcs_bridge_capability_response bridge_response{};
    int32_t bridge_status = wcs::kStatusUnavailable;
    std::string bridge_error;
    if (!wcs::bridge_exchange(
            process.process_id,
            WCS_BRIDGE_CAPABILITY,
            options->operation_nonce,
            &bridge_request,
            sizeof(bridge_request),
            &bridge_response,
            sizeof(bridge_response),
            &bridge_status,
            &bridge_error)) {
        result->reason = WCE_MOMENTS_HOOK_NOT_FOUND;
        return true;
    }
    if (bridge_status != wcs::kStatusOk || bridge_response.ready != 1u ||
        bridge_response.reason != WCE_MOMENTS_READY ||
        bridge_response.version_verified > 1u) {
        result->reason = bridge_response.reason <= WCE_MOMENTS_INTERNAL
            ? bridge_response.reason
            : WCE_MOMENTS_HOOK_VALIDATION_FAILED;
        return true;
    }
    std::string actual_account;
    if (!read_fixed(bridge_response.actual_account, &actual_account)) {
        result->reason = WCE_MOMENTS_HOOK_VALIDATION_FAILED;
        return true;
    }
    if (actual_account != options->account_utf8) {
        result->reason = WCE_MOMENTS_ACCOUNT_MISMATCH;
        return true;
    }
    result->reason = WCE_MOMENTS_READY;
    result->ready = 1;
    result->version_verified =
        adapter->status == wcs::AdapterStatus::Verified &&
        bridge_response.version_verified == 1u
        ? 1u
        : 0u;
    return true;
}

bool valid_request_options(const wce_wechat_moments_sync_request_options *options) {
    return options != nullptr && options->struct_size == sizeof(*options) &&
        options->reserved == 0 && options->request_handle != 0 &&
        options->operation_nonce != 0;
}

bool valid_poll_response(const wcs_bridge_poll_response &response) {
    std::string cursor;
    if (!read_fixed(response.next_cursor, &cursor) || response.state < WCE_MOMENTS_PENDING ||
        response.state > WCE_MOMENTS_PAUSED || response.reason > WCE_MOMENTS_INTERNAL ||
        response.completion_reason > WCE_MOMENTS_COMPLETION_INTERRUPTED ||
        response.source_complete > 1u || response.version_verified > 1u ||
        response.has_more > 1u ||
        (response.source_complete == 1u && response.has_more == 1u) ||
        (response.has_more == 1u && cursor.empty()) ||
        (response.state == WCE_MOMENTS_SUCCEEDED && response.source_complete != 1u) ||
        (response.source_complete == 1u &&
         response.completion_reason == WCE_MOMENTS_COMPLETION_NONE)) {
        return false;
    }
    return true;
}

}  // namespace

extern "C" {

uint32_t WCS_CALL wcs_wechat_sns_abi_version(void) {
    return 1;
}

const char *WCS_CALL wce_status_message(int32_t status) {
    switch (status) {
        case wcs::kStatusOk: return "ok";
        case wcs::kStatusInvalidArgument: return "invalid argument";
        case wcs::kStatusUnavailable: return "unavailable";
        case wcs::kStatusProtocol: return "protocol error";
        case wcs::kStatusIo: return "I/O error";
        case wcs::kStatusNotFound: return "not found";
        case wcs::kStatusBusy: return "busy";
        case wcs::kStatusUnsupported: return "unsupported";
        case wcs::kStatusTimeout: return "timeout";
        default: return "native SNS error";
    }
}

int32_t WCS_CALL wce_wechat_moments_sync_get_capability(
    void *,
    const wce_wechat_moments_sync_capability_options *options,
    wce_wechat_moments_sync_capability *result) {
    if (options == nullptr || result == nullptr ||
        options->struct_size != sizeof(*options) ||
        result->struct_size != sizeof(*result) || (options->flags & ~1u) != 0 ||
        options->operation_nonce == 0 ||
        !valid_text(options->account_utf8, 255) ||
        !valid_text(options->account_directory_utf8, 32768) ||
        !std::filesystem::path(options->account_directory_utf8).is_absolute()) {
        return wcs::kStatusInvalidArgument;
    }
    decide_capability(options, result);
    return wcs::kStatusOk;
}

int32_t WCS_CALL wce_wechat_moments_sync_begin(
    void *,
    const wce_wechat_moments_sync_begin_options *options,
    uint64_t *request_handle) {
    if (options == nullptr || request_handle == nullptr ||
        options->struct_size != sizeof(*options) || (options->flags & ~3u) != 0 ||
        options->operation_nonce == 0 || options->scene != 1 ||
        !valid_text(options->account_utf8, 255) ||
        !valid_text(options->account_directory_utf8, 32768) ||
        !std::filesystem::path(options->account_directory_utf8).is_absolute() ||
        !valid_text(options->target_username_utf8, 255) ||
        !valid_text(options->resume_cursor_utf8, 255, true)) {
        return wcs::kStatusInvalidArgument;
    }
    wce_wechat_moments_sync_capability_options capability_options{};
    capability_options.struct_size = sizeof(capability_options);
    capability_options.flags = (options->flags & 2u) != 0 ? 1u : 0u;
    capability_options.account_utf8 = options->account_utf8;
    capability_options.account_directory_utf8 = options->account_directory_utf8;
    capability_options.operation_nonce = options->operation_nonce;
    wce_wechat_moments_sync_capability capability{};
    capability.struct_size = sizeof(capability);
    decide_capability(&capability_options, &capability);
    if (capability.ready != 1u) {
        return wcs::kStatusUnavailable;
    }

    wcs::ProcessInfo process;
    std::string process_error;
    if (!wcs::probe_wechat(&process, &process_error) || process.process_id == 0) {
        return wcs::kStatusUnavailable;
    }
    wcs_bridge_begin_request bridge_request{};
    bridge_request.flags = options->flags;
    bridge_request.scene = options->scene;
    bridge_request.page_size = options->page_size;
    if (!wcs::copy_fixed(bridge_request.account, options->account_utf8) ||
        !wcs::copy_fixed(bridge_request.target_username, options->target_username_utf8) ||
        (options->resume_cursor_utf8 != nullptr &&
         !wcs::copy_fixed(bridge_request.resume_cursor, options->resume_cursor_utf8)) ||
        !wcs::copy_fixed(bridge_request.binary_sha256, process.binary_sha256) ||
        !wcs::copy_fixed(bridge_request.adapter_id, std::string(capability.adapter_id))) {
        return wcs::kStatusInvalidArgument;
    }
    wcs_bridge_begin_response bridge_response{};
    int32_t bridge_status = wcs::kStatusUnavailable;
    std::string bridge_error;
    if (!wcs::bridge_exchange(
            process.process_id,
            WCS_BRIDGE_BEGIN,
            options->operation_nonce,
            &bridge_request,
            sizeof(bridge_request),
            &bridge_response,
            sizeof(bridge_response),
            &bridge_status,
            &bridge_error)) {
        return wcs::kStatusUnavailable;
    }
    if (bridge_status != wcs::kStatusOk || bridge_response.request_handle == 0 ||
        bridge_response.reason != WCE_MOMENTS_READY) {
        return bridge_status == wcs::kStatusOk ? wcs::kStatusProtocol : bridge_status;
    }
    uint64_t local_handle = g_next_handle.fetch_add(1);
    if (local_handle == 0) {
        local_handle = g_next_handle.fetch_add(1);
    }
    {
        std::lock_guard<std::mutex> lock(g_requests_mutex);
        g_requests.emplace(local_handle, ActiveRequest{
            process.process_id,
            bridge_response.request_handle,
            capability.version_verified == 1u,
        });
    }
    *request_handle = local_handle;
    return wcs::kStatusOk;
}

int32_t WCS_CALL wce_wechat_moments_sync_poll(
    void *,
    const wce_wechat_moments_sync_request_options *options,
    wce_wechat_moments_sync_poll_result *result) {
    if (!valid_request_options(options) || result == nullptr ||
        result->struct_size != sizeof(*result)) {
        return wcs::kStatusInvalidArgument;
    }
    ActiveRequest active;
    {
        std::lock_guard<std::mutex> lock(g_requests_mutex);
        const auto iterator = g_requests.find(options->request_handle);
        if (iterator == g_requests.end()) {
            return wcs::kStatusNotFound;
        }
        active = iterator->second;
    }
    wcs_bridge_request bridge_request{active.remote_handle};
    wcs_bridge_poll_response bridge_response{};
    int32_t bridge_status = wcs::kStatusUnavailable;
    std::string bridge_error;
    if (!wcs::bridge_exchange(
            active.process_id,
            WCS_BRIDGE_POLL,
            options->operation_nonce,
            &bridge_request,
            sizeof(bridge_request),
            &bridge_response,
            sizeof(bridge_response),
            &bridge_status,
            &bridge_error)) {
        reset_poll(result);
        result->state = WCE_MOMENTS_PAUSED;
        result->reason = WCE_MOMENTS_WECHAT_NOT_RUNNING;
        result->terminal_status = wcs::kStatusUnavailable;
        result->completion_reason = WCE_MOMENTS_COMPLETION_INTERRUPTED;
        result->version_verified = active.version_verified ? 1u : 0u;
        return wcs::kStatusOk;
    }
    if (bridge_status != wcs::kStatusOk) {
        return bridge_status;
    }
    if (!valid_poll_response(bridge_response)) {
        return wcs::kStatusProtocol;
    }
    reset_poll(result);
    result->state = bridge_response.state;
    result->reason = bridge_response.reason;
    result->terminal_status = bridge_response.terminal_status;
    result->completion_reason = bridge_response.completion_reason;
    result->source_complete = bridge_response.source_complete;
    result->version_verified = bridge_response.version_verified;
    result->has_more = bridge_response.has_more;
    result->pages_fetched = bridge_response.pages_fetched;
    result->posts_observed = bridge_response.posts_observed;
    result->rows_written = bridge_response.rows_written;
    result->oldest_tid = bridge_response.oldest_tid;
    std::memcpy(result->next_cursor, bridge_response.next_cursor, sizeof(result->next_cursor));
    return wcs::kStatusOk;
}

int32_t WCS_CALL wce_wechat_moments_sync_cancel(
    void *,
    const wce_wechat_moments_sync_request_options *options) {
    if (!valid_request_options(options)) {
        return wcs::kStatusInvalidArgument;
    }
    ActiveRequest active;
    {
        std::lock_guard<std::mutex> lock(g_requests_mutex);
        const auto iterator = g_requests.find(options->request_handle);
        if (iterator == g_requests.end()) {
            return wcs::kStatusNotFound;
        }
        active = iterator->second;
    }
    wcs_bridge_request request{active.remote_handle};
    wcs_bridge_simple_response response{};
    int32_t response_status = wcs::kStatusUnavailable;
    std::string error;
    if (!wcs::bridge_exchange(
            active.process_id,
            WCS_BRIDGE_CANCEL,
            options->operation_nonce,
            &request,
            sizeof(request),
            &response,
            sizeof(response),
            &response_status,
            &error)) {
        return wcs::kStatusUnavailable;
    }
    return response_status;
}

int32_t WCS_CALL wce_wechat_moments_sync_close(
    void *,
    const wce_wechat_moments_sync_request_options *options) {
    if (!valid_request_options(options)) {
        return wcs::kStatusInvalidArgument;
    }
    ActiveRequest active;
    {
        std::lock_guard<std::mutex> lock(g_requests_mutex);
        const auto iterator = g_requests.find(options->request_handle);
        if (iterator == g_requests.end()) {
            return wcs::kStatusNotFound;
        }
        active = iterator->second;
        g_requests.erase(iterator);
    }
    wcs_bridge_request request{active.remote_handle};
    wcs_bridge_simple_response response{};
    int32_t response_status = wcs::kStatusUnavailable;
    std::string error;
    // Closing a local handle is final even if WeChat exited before acknowledging it.
    wcs::bridge_exchange(
        active.process_id,
        WCS_BRIDGE_CLOSE,
        options->operation_nonce,
        &request,
        sizeof(request),
        &response,
        sizeof(response),
        &response_status,
        &error);
    return wcs::kStatusOk;
}

}  // extern "C"
