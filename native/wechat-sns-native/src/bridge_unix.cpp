#include "internal.hpp"

#if !defined(_WIN32)

#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <string>

namespace wcs {
namespace {

std::string socket_path(uint32_t process_id) {
    const char *explicit_path = std::getenv("WECHAT_SNS_NATIVE_BRIDGE");
    if (explicit_path != nullptr && explicit_path[0] != '\0') {
        std::string path(explicit_path);
        const std::string marker = "{pid}";
        const size_t offset = path.find(marker);
        if (offset != std::string::npos) {
            path.replace(offset, marker.size(), std::to_string(process_id));
        }
        return path;
    }
    return "/tmp/wechat-sns-native-" + std::to_string(getuid()) + "-" +
        std::to_string(process_id) + ".sock";
}

bool write_all(int socket_fd, const void *data, size_t size) {
    const auto *cursor = static_cast<const uint8_t *>(data);
    while (size > 0) {
        const ssize_t written = send(socket_fd, cursor, size, 0);
        if (written <= 0) {
            return false;
        }
        cursor += written;
        size -= static_cast<size_t>(written);
    }
    return true;
}

bool read_all(int socket_fd, void *data, size_t size) {
    auto *cursor = static_cast<uint8_t *>(data);
    while (size > 0) {
        const ssize_t received = recv(socket_fd, cursor, size, 0);
        if (received <= 0) {
            return false;
        }
        cursor += received;
        size -= static_cast<size_t>(received);
    }
    return true;
}

}  // namespace

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
    const std::string path = socket_path(process_id);
    struct stat socket_stat {};
    if (lstat(path.c_str(), &socket_stat) != 0 || !S_ISSOCK(socket_stat.st_mode) ||
        socket_stat.st_uid != getuid() || (socket_stat.st_mode & 0077) != 0) {
        if (error != nullptr) {
            *error = "authenticated WeChat bridge socket is unavailable";
        }
        return false;
    }
    if (path.size() >= sizeof(sockaddr_un::sun_path)) {
        if (error != nullptr) {
            *error = "WeChat bridge socket path is too long";
        }
        return false;
    }
    const int socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socket_fd < 0) {
        if (error != nullptr) {
            *error = "unable to create WeChat bridge socket";
        }
        return false;
    }
    timeval timeout{};
    timeout.tv_sec = 1;
    timeout.tv_usec = 500000;
    setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(socket_fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    if (connect(socket_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0) {
        close(socket_fd);
        if (error != nullptr) {
            *error = "unable to connect to the WeChat bridge";
        }
        return false;
    }
#if defined(__APPLE__) || defined(__FreeBSD__)
    uid_t peer_uid = 0;
    gid_t peer_gid = 0;
    if (getpeereid(socket_fd, &peer_uid, &peer_gid) != 0 || peer_uid != getuid()) {
        close(socket_fd);
        if (error != nullptr) {
            *error = "WeChat bridge peer identity did not match the current user";
        }
        return false;
    }
#endif
#if defined(__APPLE__)
    pid_t peer_process_id = 0;
    socklen_t peer_process_id_size = sizeof(peer_process_id);
    if (getsockopt(
            socket_fd,
            SOL_LOCAL,
            LOCAL_PEERPID,
            &peer_process_id,
            &peer_process_id_size) != 0 ||
        peer_process_id != static_cast<pid_t>(process_id)) {
        close(socket_fd);
        if (error != nullptr) {
            *error = "WeChat bridge peer process identity did not match";
        }
        return false;
    }
#endif
    const wcs_bridge_frame_header request_header{
        WCS_BRIDGE_MAGIC,
        WCS_BRIDGE_VERSION,
        command,
        request_size,
        0,
        operation_nonce,
    };
    const bool sent = write_all(socket_fd, &request_header, sizeof(request_header)) &&
        write_all(socket_fd, request, request_size);
    wcs_bridge_frame_header response_header{};
    const bool header_received = sent &&
        read_all(socket_fd, &response_header, sizeof(response_header));
    const bool header_valid = header_received &&
        response_header.magic == WCS_BRIDGE_MAGIC &&
        response_header.version == WCS_BRIDGE_VERSION &&
        response_header.command == command &&
        response_header.operation_nonce == operation_nonce &&
        response_header.payload_size == response_size;
    const bool payload_received = header_valid &&
        (response_size == 0 || read_all(socket_fd, response, response_size));
    close(socket_fd);
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
