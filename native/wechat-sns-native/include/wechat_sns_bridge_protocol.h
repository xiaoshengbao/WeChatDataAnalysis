#ifndef WECHAT_SNS_BRIDGE_PROTOCOL_H
#define WECHAT_SNS_BRIDGE_PROTOCOL_H

#include <stdint.h>

#define WCS_BRIDGE_MAGIC 0x31534357u
#define WCS_BRIDGE_VERSION 1u

enum wcs_bridge_command {
    WCS_BRIDGE_CAPABILITY = 1,
    WCS_BRIDGE_BEGIN = 2,
    WCS_BRIDGE_POLL = 3,
    WCS_BRIDGE_CANCEL = 4,
    WCS_BRIDGE_CLOSE = 5,
};

#pragma pack(push, 1)
typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t command;
    uint32_t payload_size;
    int32_t status;
    uint64_t operation_nonce;
} wcs_bridge_frame_header;

typedef struct {
    uint32_t wechat_process_id;
    uint32_t flags;
    char account[256];
    char binary_sha256[65];
    char adapter_id[64];
} wcs_bridge_capability_request;

typedef struct {
    uint32_t reason;
    uint32_t ready;
    uint32_t version_verified;
    uint32_t reserved;
    char actual_account[256];
} wcs_bridge_capability_response;

typedef struct {
    uint32_t flags;
    uint32_t scene;
    uint32_t page_size;
    uint32_t reserved;
    char account[256];
    char target_username[256];
    char resume_cursor[256];
    char binary_sha256[65];
    char adapter_id[64];
} wcs_bridge_begin_request;

typedef struct {
    uint64_t request_handle;
    uint32_t reason;
    uint32_t reserved;
} wcs_bridge_begin_response;

typedef struct {
    uint64_t request_handle;
} wcs_bridge_request;

typedef struct {
    uint32_t state;
    uint32_t reason;
    int32_t terminal_status;
    uint32_t completion_reason;
    uint32_t source_complete;
    uint32_t version_verified;
    uint32_t has_more;
    uint32_t reserved;
    uint64_t pages_fetched;
    uint64_t posts_observed;
    uint64_t rows_written;
    uint64_t oldest_tid;
    char next_cursor[256];
} wcs_bridge_poll_response;

typedef struct {
    uint32_t reason;
    uint32_t reserved;
} wcs_bridge_simple_response;
#pragma pack(pop)

#endif
