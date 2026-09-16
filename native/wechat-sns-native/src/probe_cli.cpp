#include "wechat_sns_native.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

std::string json_escape(const std::string &value) {
    std::string result;
    for (char character : value) {
        if (character == '"' || character == '\\') {
            result.push_back('\\');
        }
        result.push_back(character);
    }
    return result;
}

}  // namespace

int main(int argc, char **argv) {
    const std::string account = argc > 1 ? argv[1] : "probe";
    const std::string account_directory = argc > 2 ? argv[2] : "/tmp/wcs-probe";
    wce_wechat_moments_sync_capability_options options{};
    options.struct_size = sizeof(options);
    options.flags = 1;
    options.account_utf8 = account.c_str();
    options.account_directory_utf8 = account_directory.c_str();
    options.operation_nonce = 1;
    wce_wechat_moments_sync_capability capability{};
    capability.struct_size = sizeof(capability);
    const int32_t status =
        wce_wechat_moments_sync_get_capability(nullptr, &options, &capability);
    std::cout << "{\"abiVersion\":" << wcs_wechat_sns_abi_version()
              << ",\"status\":" << status
              << ",\"reason\":" << capability.reason
              << ",\"platformSupported\":" << capability.platform_supported
              << ",\"ready\":" << capability.ready
              << ",\"versionVerified\":" << capability.version_verified
              << ",\"wechatProcessId\":" << capability.wechat_process_id
              << ",\"wechatVersion\":\""
              << json_escape(capability.actual_wechat_version)
              << "\",\"adapterId\":\"" << json_escape(capability.adapter_id)
              << "\"}\n";
    return status == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
