#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "../v8-native-common/windows.h"
#include <wtsapi32.h>
#include <cstdint>
#include <string>
#include <vector>

namespace v8unlock {
using v8native::Handle;
using v8native::now_ms;
using v8native::token_sid;
using v8native::process_sid;
using v8native::pipe_io;
using v8native::connect_pipe;
inline constexpr wchar_t kClsid[] = L"{793345F6-C96B-472A-A780-387839440068}";
inline constexpr wchar_t kRegistryRoot[] = L"SOFTWARE\\V8AgentOS\\SessionUnlock";
inline constexpr uint32_t kProtocol = 1;
inline constexpr uint32_t kMagic = 0x55385631;
inline constexpr uint64_t kMaxLifetimeMs = 60000;
inline constexpr size_t kMaxJsonBytes = 16384;

enum class Code : uint32_t {
    accepted, unlocked, already_unlocked, invalid_request, expired_request,
    replayed_request, wrong_session, wrong_user, remote_session, state_unknown,
    provider_unavailable, peer_untrusted, busy, authentication_failed,
    password_expired, account_restricted, timeout, cancelled, internal_error,
    replay_store_unavailable, unsupported_account, locked, outcome_unknown
};
const char* code_name(Code code);

// Fixed UTF-16 wire envelope: never dump or log this type.
struct Request {
    uint32_t magic = kMagic;
    uint32_t version = kProtocol;
    uint32_t size = sizeof(Request);
    uint32_t session_id = 0;
    uint64_t expires_at = 0;
    wchar_t request_id[37]{};
    wchar_t username[257]{};
    wchar_t domain[257]{};
    wchar_t password[1025]{};
};
struct Reply {
    uint32_t magic = kMagic;
    uint32_t version = kProtocol;
    Code code = Code::internal_error;
    uint32_t submitted = 0;
};
static_assert(sizeof(wchar_t) == 2 && sizeof(Request) == 3176 && sizeof(Reply) == 16,
              "Protocol v1 requires the same fixed Windows x64/ARM64 wire layout");
class SecretRequest {
public:
    Request value;
    SecretRequest() = default;
    SecretRequest(const SecretRequest&) = delete;
    SecretRequest& operator=(const SecretRequest&) = delete;
    ~SecretRequest() { SecureZeroMemory(&value, sizeof(value)); }
};
struct Session {
    DWORD id = 0;
    bool known = false;
    bool locked = false;
    bool local = false;
    bool logged_on = false;
    std::wstring sid;
    std::wstring username;
    std::wstring domain;
};
DWORD current_session_id();
Session inspect_session(DWORD id);
std::wstring account_sid(const std::wstring& domain, const std::wstring& username);
std::wstring pipe_name(DWORD session);
Code parse_request(const std::string& json, Request& output);
Code validate_request(const Request& request, uint64_t now);
Code validate_target(const Request& request, const Session& session);
bool verify_pipe_client(HANDLE pipe, const Session& session);
bool verify_pipe_server(HANDLE pipe, DWORD session);
bool provider_registered();
Code claim_request(const Request& request);
void print_result(Code code, DWORD session, int submitted, bool installed, bool ready = false, const Session* observed = nullptr);
}  // namespace v8unlock
