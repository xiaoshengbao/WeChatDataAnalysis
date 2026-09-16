#ifndef WECHAT_SNS_NATIVE_H
#define WECHAT_SNS_NATIVE_H

#include <stdint.h>

#if defined(_WIN32)
#define WCS_EXPORT __declspec(dllexport)
#define WCS_CALL __cdecl
#else
#define WCS_EXPORT __attribute__((visibility("default")))
#define WCS_CALL
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum wce_wechat_moments_reason {
    WCE_MOMENTS_READY = 0,
    WCE_MOMENTS_UNSUPPORTED_PLATFORM = 1,
    WCE_MOMENTS_UNSUPPORTED_ARCHITECTURE = 2,
    WCE_MOMENTS_RUNTIME_UNAVAILABLE = 3,
    WCE_MOMENTS_WECHAT_NOT_RUNNING = 4,
    WCE_MOMENTS_WECHAT_NOT_LOGGED_IN = 5,
    WCE_MOMENTS_ACCOUNT_UNVERIFIED = 6,
    WCE_MOMENTS_ACCOUNT_MISMATCH = 7,
    WCE_MOMENTS_WECHAT_VERSION_UNVERIFIED = 8,
    WCE_MOMENTS_HOOK_NOT_FOUND = 9,
    WCE_MOMENTS_HOOK_VALIDATION_FAILED = 10,
    WCE_MOMENTS_BUSY = 11,
    WCE_MOMENTS_TARGET_NOT_FOUND = 12,
    WCE_MOMENTS_ACCESS_DENIED = 13,
    WCE_MOMENTS_DATABASE_WRITE_FAILED = 14,
    WCE_MOMENTS_TIMEOUT = 15,
    WCE_MOMENTS_CANCELLED = 16,
    WCE_MOMENTS_INTERNAL = 17,
};

enum wce_wechat_moments_request_state {
    WCE_MOMENTS_PENDING = 1,
    WCE_MOMENTS_RUNNING = 2,
    WCE_MOMENTS_SUCCEEDED = 3,
    WCE_MOMENTS_FAILED = 4,
    WCE_MOMENTS_CANCELLED_STATE = 5,
    WCE_MOMENTS_PAUSED = 6,
};

enum wce_wechat_moments_completion_reason {
    WCE_MOMENTS_COMPLETION_NONE = 0,
    WCE_MOMENTS_COMPLETION_SOURCE_END = 1,
    WCE_MOMENTS_COMPLETION_EMPTY = 2,
    WCE_MOMENTS_COMPLETION_VISIBILITY_BOUNDARY = 3,
    WCE_MOMENTS_COMPLETION_ACCESS_DENIED = 4,
    WCE_MOMENTS_COMPLETION_CANCELLED = 5,
    WCE_MOMENTS_COMPLETION_INTERRUPTED = 6,
};

typedef struct {
    uint32_t struct_size;
    uint32_t flags;
    const char *account_utf8;
    const char *account_directory_utf8;
    uint64_t operation_nonce;
} wce_wechat_moments_sync_capability_options;

typedef struct {
    uint32_t struct_size;
    uint32_t reason;
    uint32_t platform_supported;
    uint32_t ready;
    uint32_t version_verified;
    uint32_t wechat_process_id;
    char actual_wechat_version[32];
    char adapter_id[64];
} wce_wechat_moments_sync_capability;

typedef struct {
    uint32_t struct_size;
    uint32_t flags;
    const char *account_utf8;
    const char *account_directory_utf8;
    const char *target_username_utf8;
    const char *resume_cursor_utf8;
    uint64_t operation_nonce;
    uint32_t scene;
    uint32_t page_size;
} wce_wechat_moments_sync_begin_options;

typedef struct {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t request_handle;
    uint64_t operation_nonce;
} wce_wechat_moments_sync_request_options;

typedef struct {
    uint32_t struct_size;
    uint32_t state;
    uint32_t reason;
    int32_t terminal_status;
    uint32_t completion_reason;
    uint32_t source_complete;
    uint32_t version_verified;
    uint32_t has_more;
    uint64_t pages_fetched;
    uint64_t posts_observed;
    uint64_t rows_written;
    uint64_t oldest_tid;
    char next_cursor[256];
} wce_wechat_moments_sync_poll_result;

WCS_EXPORT uint32_t WCS_CALL wcs_wechat_sns_abi_version(void);
WCS_EXPORT const char *WCS_CALL wce_status_message(int32_t status);

WCS_EXPORT int32_t WCS_CALL wce_wechat_moments_sync_get_capability(
    void *client,
    const wce_wechat_moments_sync_capability_options *options,
    wce_wechat_moments_sync_capability *result);

WCS_EXPORT int32_t WCS_CALL wce_wechat_moments_sync_begin(
    void *client,
    const wce_wechat_moments_sync_begin_options *options,
    uint64_t *request_handle);

WCS_EXPORT int32_t WCS_CALL wce_wechat_moments_sync_poll(
    void *client,
    const wce_wechat_moments_sync_request_options *options,
    wce_wechat_moments_sync_poll_result *result);

WCS_EXPORT int32_t WCS_CALL wce_wechat_moments_sync_cancel(
    void *client,
    const wce_wechat_moments_sync_request_options *options);

WCS_EXPORT int32_t WCS_CALL wce_wechat_moments_sync_close(
    void *client,
    const wce_wechat_moments_sync_request_options *options);

#ifdef __cplusplus
}

static_assert(sizeof(wce_wechat_moments_sync_capability_options) == 32);
static_assert(sizeof(wce_wechat_moments_sync_capability) == 120);
static_assert(sizeof(wce_wechat_moments_sync_begin_options) == 56);
static_assert(sizeof(wce_wechat_moments_sync_request_options) == 24);
static_assert(sizeof(wce_wechat_moments_sync_poll_result) == 320);
#endif

#endif
