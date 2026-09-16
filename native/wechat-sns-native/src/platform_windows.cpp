#include "internal.hpp"

#if defined(_WIN32)

#include <bcrypt.h>
#include <windows.h>
#include <tlhelp32.h>

#include <array>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <vector>

namespace wcs {
namespace {

std::string utf8(const std::wstring &value) {
    if (value.empty()) {
        return {};
    }
    const int size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
        nullptr, 0, nullptr, nullptr);
    if (size <= 0) {
        return {};
    }
    std::string result(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
            result.data(), size, nullptr, nullptr) != size) {
        return {};
    }
    return result;
}

std::string file_version(const std::wstring &path) {
    DWORD ignored = 0;
    const DWORD size = GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (size == 0) {
        return {};
    }
    std::vector<uint8_t> buffer(size);
    if (!GetFileVersionInfoW(path.c_str(), 0, size, buffer.data())) {
        return {};
    }
    VS_FIXEDFILEINFO *info = nullptr;
    UINT info_size = 0;
    if (!VerQueryValueW(
            buffer.data(), L"\\", reinterpret_cast<void **>(&info), &info_size) ||
        info == nullptr || info_size < sizeof(*info)) {
        return {};
    }
    std::ostringstream version;
    version << HIWORD(info->dwFileVersionMS) << '.' << LOWORD(info->dwFileVersionMS)
            << '.' << HIWORD(info->dwFileVersionLS) << '.'
            << LOWORD(info->dwFileVersionLS);
    return version.str();
}

std::string sha256_file(const std::wstring &path) {
    FILE *file = nullptr;
    if (_wfopen_s(&file, path.c_str(), L"rb") != 0 || file == nullptr) {
        return {};
    }
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_size = 0;
    DWORD hash_size = 0;
    DWORD copied = 0;
    std::vector<uint8_t> object;
    std::vector<uint8_t> digest;
    bool ok = BCryptOpenAlgorithmProvider(
                  &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0 &&
        BCryptGetProperty(
            algorithm, BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&object_size), sizeof(object_size), &copied, 0) == 0 &&
        BCryptGetProperty(
            algorithm, BCRYPT_HASH_LENGTH,
            reinterpret_cast<PUCHAR>(&hash_size), sizeof(hash_size), &copied, 0) == 0;
    if (ok) {
        object.resize(object_size);
        digest.resize(hash_size);
        ok = BCryptCreateHash(
                 algorithm, &hash, object.data(), object_size, nullptr, 0, 0) == 0;
    }
    std::array<uint8_t, 1024 * 1024> buffer{};
    while (ok) {
        const size_t count = std::fread(buffer.data(), 1, buffer.size(), file);
        if (count > 0) {
            ok = BCryptHashData(
                     hash, buffer.data(), static_cast<ULONG>(count), 0) == 0;
        }
        if (count < buffer.size()) {
            break;
        }
    }
    if (std::ferror(file) != 0) {
        ok = false;
    }
    if (ok) {
        ok = BCryptFinishHash(hash, digest.data(), hash_size, 0) == 0;
    }
    std::fclose(file);
    if (hash != nullptr) {
        BCryptDestroyHash(hash);
    }
    if (algorithm != nullptr) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    if (!ok || digest.size() != 32) {
        return {};
    }
    std::ostringstream result;
    for (uint8_t byte : digest) {
        result << std::hex << std::setfill('0') << std::setw(2)
               << static_cast<unsigned int>(byte);
    }
    return result.str();
}

bool write_all(HANDLE pipe, const void *data, size_t size) {
    const auto *cursor = static_cast<const uint8_t *>(data);
    while (size > 0) {
        DWORD written = 0;
        const DWORD chunk = static_cast<DWORD>(
            size > MAXDWORD ? MAXDWORD : size);
        if (!WriteFile(pipe, cursor, chunk, &written, nullptr) || written == 0) {
            return false;
        }
        cursor += written;
        size -= written;
    }
    return true;
}

bool read_all(HANDLE pipe, void *data, size_t size) {
    auto *cursor = static_cast<uint8_t *>(data);
    while (size > 0) {
        DWORD received = 0;
        const DWORD chunk = static_cast<DWORD>(
            size > MAXDWORD ? MAXDWORD : size);
        if (!ReadFile(pipe, cursor, chunk, &received, nullptr) || received == 0) {
            return false;
        }
        cursor += received;
        size -= received;
    }
    return true;
}

}  // namespace

bool probe_wechat(ProcessInfo *result, std::string *error) {
    if (result == nullptr) {
        return false;
    }
    *result = ProcessInfo{};
    result->platform = "windows";
#if defined(_M_X64) || defined(__x86_64__)
    result->architecture = "x86_64";
#else
    result->architecture = "unknown";
#endif
    result->platform_supported = true;
    result->architecture_supported = result->architecture == "x86_64";
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        if (error != nullptr) {
            *error = "unable to enumerate processes";
        }
        return false;
    }
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (_wcsicmp(entry.szExeFile, L"Weixin.exe") == 0 ||
                _wcsicmp(entry.szExeFile, L"WeChat.exe") == 0) {
                result->process_id = entry.th32ProcessID;
                HANDLE process = OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ProcessID);
                if (process != nullptr) {
                    std::vector<wchar_t> path(32768);
                    DWORD path_size = static_cast<DWORD>(path.size());
                    if (QueryFullProcessImageNameW(
                            process, 0, path.data(), &path_size)) {
                        const std::wstring executable(path.data(), path_size);
                        result->executable_path = utf8(executable);
                        result->version = file_version(executable);
                        result->binary_sha256 = sha256_file(executable);
                    }
                    CloseHandle(process);
                }
                break;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    if (result->process_id != 0) {
        if (result->executable_path.empty() || result->version.empty() ||
            result->binary_sha256.empty()) {
            if (error != nullptr) {
                *error = "unable to read the running WeChat executable identity";
            }
            return false;
        }
    }
    return true;
}

bool bridge_exchange(
    uint32_t process_id,
    uint16_t command,
    uint64_t operation_nonce,
    const void *request,
    uint32_t request_size,
    void *response,
    uint32_t response_size,
    int32_t *response_status,
    std::string *error) {
    if (process_id == 0 || operation_nonce == 0 || request == nullptr ||
        (response_size > 0 && response == nullptr) || response_status == nullptr) {
        if (error != nullptr) {
            *error = "invalid bridge exchange arguments";
        }
        return false;
    }
    const std::wstring pipe_path = L"\\\\.\\pipe\\wechat-sns-native-" +
        std::to_wstring(process_id);
    if (!WaitNamedPipeW(pipe_path.c_str(), 1500)) {
        if (error != nullptr) {
            *error = "WeChat bridge named pipe is unavailable";
        }
        return false;
    }
    HANDLE pipe = CreateFileW(
        pipe_path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        if (error != nullptr) {
            *error = "unable to connect to the WeChat bridge named pipe";
        }
        return false;
    }
    ULONG server_process_id = 0;
    if (!GetNamedPipeServerProcessId(pipe, &server_process_id) ||
        server_process_id != process_id) {
        CloseHandle(pipe);
        if (error != nullptr) {
            *error = "WeChat bridge server process identity did not match";
        }
        return false;
    }
    const wcs_bridge_frame_header request_header{
        WCS_BRIDGE_MAGIC,
        WCS_BRIDGE_VERSION,
        command,
        request_size,
        0,
        operation_nonce,
    };
    const bool sent = write_all(pipe, &request_header, sizeof(request_header)) &&
        write_all(pipe, request, request_size);
    wcs_bridge_frame_header response_header{};
    const bool header_received = sent &&
        read_all(pipe, &response_header, sizeof(response_header));
    const bool header_valid = header_received &&
        response_header.magic == WCS_BRIDGE_MAGIC &&
        response_header.version == WCS_BRIDGE_VERSION &&
        response_header.command == command &&
        response_header.operation_nonce == operation_nonce &&
        response_header.payload_size == response_size;
    const bool payload_received = header_valid &&
        (response_size == 0 || read_all(pipe, response, response_size));
    CloseHandle(pipe);
    if (!payload_received) {
        if (error != nullptr) {
            *error = "WeChat bridge returned an invalid or incomplete frame";
        }
        return false;
    }
    *response_status = response_header.status;
    return true;
}

}  // namespace wcs

#endif
