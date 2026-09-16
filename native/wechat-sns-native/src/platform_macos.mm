#include "internal.hpp"

#import <Foundation/Foundation.h>

#include <CommonCrypto/CommonDigest.h>
#include <libproc.h>

#include <array>
#include <cstdio>
#include <filesystem>
#include <iomanip>
#include <sstream>
#include <vector>

namespace wcs {
namespace {

std::string sha256_file(const std::string &path) {
    FILE *file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return {};
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    std::array<unsigned char, 1024 * 1024> buffer{};
    while (true) {
        const size_t count = std::fread(buffer.data(), 1, buffer.size(), file);
        if (count > 0) {
            CC_SHA256_Update(&context, buffer.data(), static_cast<CC_LONG>(count));
        }
        if (count < buffer.size()) {
            break;
        }
    }
    const bool read_ok = std::ferror(file) == 0;
    std::fclose(file);
    if (!read_ok) {
        return {};
    }
    std::array<unsigned char, CC_SHA256_DIGEST_LENGTH> digest{};
    CC_SHA256_Final(digest.data(), &context);
    std::ostringstream result;
    for (unsigned char byte : digest) {
        result << std::hex << std::setfill('0') << std::setw(2)
               << static_cast<unsigned int>(byte);
    }
    return result.str();
}

std::string architecture_name() {
#if defined(__arm64__) || defined(__aarch64__)
    return "arm64";
#elif defined(__x86_64__)
    return "x86_64";
#else
    return "unknown";
#endif
}

}  // namespace

bool probe_wechat(ProcessInfo *result, std::string *error) {
    if (result == nullptr) {
        return false;
    }
    *result = ProcessInfo{};
    result->platform = "macos";
    result->architecture = architecture_name();
    result->platform_supported = true;
    result->architecture_supported =
        result->architecture == "arm64" || result->architecture == "x86_64";

    const int capacity = proc_listallpids(nullptr, 0);
    if (capacity <= 0) {
        if (error != nullptr) {
            *error = "unable to enumerate processes";
        }
        return false;
    }
    std::vector<pid_t> pids(static_cast<size_t>(capacity) + 32);
    const int returned_count = proc_listallpids(
        pids.data(), static_cast<int>(pids.size() * sizeof(pid_t)));
    if (returned_count <= 0) {
        if (error != nullptr) {
            *error = "unable to enumerate processes";
        }
        return false;
    }
    const size_t pid_count = static_cast<size_t>(returned_count);
    for (size_t index = 0; index < pid_count; ++index) {
        const pid_t pid = pids[index];
        if (pid <= 0) {
            continue;
        }
        std::array<char, PROC_PIDPATHINFO_MAXSIZE> path_buffer{};
        const int path_size = proc_pidpath(pid, path_buffer.data(), path_buffer.size());
        if (path_size <= 0) {
            continue;
        }
        const std::filesystem::path executable(path_buffer.data());
        const std::string executable_string = executable.string();
        if (executable.filename() != "WeChat" ||
            executable_string.find("/WeChat.app/Contents/MacOS/WeChat") == std::string::npos) {
            continue;
        }
        result->process_id = static_cast<uint32_t>(pid);
        result->executable_path = executable_string;
        result->binary_sha256 = sha256_file(executable_string);

        const size_t app_end = executable_string.find(".app/");
        if (app_end != std::string::npos) {
            const std::string bundle_path = executable_string.substr(0, app_end + 4);
            @autoreleasepool {
                NSBundle *bundle = [NSBundle bundleWithPath:
                    [NSString stringWithUTF8String:bundle_path.c_str()]];
                NSString *version = [bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
                NSString *build = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
                if (version != nil && [version UTF8String] != nullptr) {
                    result->version = [version UTF8String];
                }
                if (build != nil && [build UTF8String] != nullptr) {
                    result->build = [build UTF8String];
                }
            }
        }
        if (result->binary_sha256.empty()) {
            if (error != nullptr) {
                *error = "unable to hash the running WeChat executable";
            }
            return false;
        }
        return true;
    }
    return true;
}

}  // namespace wcs
