#pragma once
#include "../v8-native-common/windows.h"
#include <vector>

namespace v8system {
using v8native::Handle;
using v8native::now_ms;
inline constexpr wchar_t kServiceName[] = L"V8SystemOperations";
inline constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\V8AgentOS.SystemOperations.v1";
inline constexpr wchar_t kRegistryRoot[] = L"SOFTWARE\\V8AgentOS\\SystemOperations";
inline constexpr size_t kMaxRequest = 262144;
inline constexpr size_t kMaxOutput = 65536;
inline constexpr size_t kMaxReply = kMaxOutput * 12 + 8192;
inline constexpr uint32_t kMagic = 0x53385631;
struct Frame {
    uint32_t magic = kMagic, version = 1, bytes = 0;
};
struct Request {
    std::wstring command, cwd, username, domain, password, request_id;
    std::vector<std::wstring> argv;
    uint64_t expires_at = 0;
    DWORD timeout_seconds = 0;
    Request() = default;
    Request(const Request &) = delete;
    Request &operator=(const Request &) = delete;
    ~Request() {
        clear_password();
    }
    void clear_password() {
        if (!password.empty())
            SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
        password.clear();
    }
};
struct ServiceInfo {
    bool registered = false, running = false;
    DWORD pid = 0;
    std::wstring image;
};
std::string parse_request(const std::string &, Request &);
std::string validate_request(const Request &, uint64_t);
std::wstring quote_argument(const std::wstring &);
std::wstring command_line(const std::vector<std::wstring> &);
std::wstring configured_client_sid();
ServiceInfo service_info();
bool verify_server(HANDLE, const ServiceInfo &);
bool verify_client(HANDLE, const std::wstring &);
bool elevated_administrator(HANDLE);
std::string claim_request(const Request &);
std::string json_text(const std::string &);
std::string result(const std::string &code, bool executed = false, bool elevated = false,
                   bool verified = false, DWORD exit_code = 0, bool has_exit = false,
                   const std::string &out = {}, const std::string &err = {}, bool truncated = false,
                   bool tree_stopped = false, bool execution_known = true);
bool send_frame(HANDLE, const std::string &, uint64_t, HANDLE stop = nullptr,
                DWORD max_wait_ms = 60000);
bool receive_frame(HANDLE, std::string &, size_t, uint64_t, HANDLE stop = nullptr,
                   DWORD max_wait_ms = 60000);
std::string worker_execute(Request &);
int service_dispatch();
int worker_entry();
} // namespace v8system
