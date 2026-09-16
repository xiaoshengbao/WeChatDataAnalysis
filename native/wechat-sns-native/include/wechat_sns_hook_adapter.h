#ifndef WECHAT_SNS_HOOK_ADAPTER_H
#define WECHAT_SNS_HOOK_ADAPTER_H

#include <stdint.h>

#include "wechat_sns_bridge_protocol.h"

#define WCS_HOOK_ADAPTER_ABI_VERSION 1u

enum wcs_hook_validation_flag {
    WCS_HOOK_VALIDATED_FUNCTION_STRUCTURE = 1u << 0,
    WCS_HOOK_VALIDATED_PARAMETERS = 1u << 1,
    WCS_HOOK_VALIDATED_RESPONSE_CURSOR = 1u << 2,
    WCS_HOOK_VALIDATED_DATABASE_WRITE = 1u << 3,
};

#define WCS_HOOK_REQUIRED_VALIDATION_FLAGS \
    (WCS_HOOK_VALIDATED_FUNCTION_STRUCTURE | WCS_HOOK_VALIDATED_PARAMETERS | \
     WCS_HOOK_VALIDATED_RESPONSE_CURSOR | WCS_HOOK_VALIDATED_DATABASE_WRITE)

typedef struct {
    uint32_t struct_size;
    uint32_t abi_version;
    const char *adapter_id;
    const char *wechat_version;
    const char *binary_sha256;

    /* Must run inside WeChat and return every required validation bit. */
    uint32_t (*validate)(void);

    int32_t (*capability)(
        const wcs_bridge_capability_request *request,
        wcs_bridge_capability_response *response);
    int32_t (*begin)(
        const wcs_bridge_begin_request *request,
        wcs_bridge_begin_response *response);
    int32_t (*poll)(
        const wcs_bridge_request *request,
        wcs_bridge_poll_response *response);
    int32_t (*cancel)(
        const wcs_bridge_request *request,
        wcs_bridge_simple_response *response);
    int32_t (*close)(
        const wcs_bridge_request *request,
        wcs_bridge_simple_response *response);
} wcs_hook_adapter_v1;

/* Each version adapter exports exactly this factory from the in-process payload. */
typedef const wcs_hook_adapter_v1 *(*wcs_get_hook_adapter_v1_fn)(void);

#endif
