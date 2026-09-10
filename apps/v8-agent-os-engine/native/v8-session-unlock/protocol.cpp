#include "protocol.h"
#include "../v8-native-common/json_reader.h"
using v8native::JsonReader;
#include <sddl.h>
#include <aclapi.h>
#include <algorithm>
#include <cstdio>
#include <cwchar>
#include <limits>
#include <map>
#include <set>

namespace v8unlock {
const char* code_name(Code code) {
    switch (code) {
#define CODE(x) case Code::x: return #x;
        CODE(accepted) CODE(unlocked) CODE(already_unlocked) CODE(invalid_request)
        CODE(expired_request) CODE(replayed_request) CODE(wrong_session) CODE(wrong_user)
        CODE(remote_session) CODE(state_unknown) CODE(provider_unavailable) CODE(peer_untrusted)
        CODE(busy) CODE(authentication_failed) CODE(password_expired) CODE(account_restricted)
        CODE(timeout) CODE(cancelled) CODE(internal_error) CODE(replay_store_unavailable)
        CODE(unsupported_account) CODE(locked) CODE(outcome_unknown)
#undef CODE
    }
    return "internal_error";
}
DWORD current_session_id() {
    DWORD id = 0;
    return ProcessIdToSessionId(GetCurrentProcessId(), &id) ? id : 0;
}
std::wstring account_sid(const std::wstring& domain, const std::wstring& username) {
    std::wstring qualified = domain.empty() ? username : domain + L"\\" + username;
    DWORD sid_size = 0, domain_size = 0;
    SID_NAME_USE use{};
    LookupAccountNameW(nullptr, qualified.c_str(), nullptr, &sid_size, nullptr, &domain_size, &use);
    if (!sid_size || sid_size > 65536 || domain_size > 32768) return {};
    std::vector<BYTE> sid(sid_size);
    std::vector<wchar_t> resolved_domain(domain_size + 1);
    if (!LookupAccountNameW(nullptr, qualified.c_str(), sid.data(), &sid_size,
                           resolved_domain.data(), &domain_size, &use) || use != SidTypeUser) return {};
    wchar_t* text = nullptr;
    if (!ConvertSidToStringSidW(sid.data(), &text)) return {};
    std::wstring result(text);
    LocalFree(text);
    return result;
}
Session inspect_session(DWORD id) {
    Session result;
    result.id = id;
    if (!id) return result;
    wchar_t* raw = nullptr;
    DWORD size = 0;
    if (!WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, id, WTSClientProtocolType, &raw, &size)) return result;
    result.local = size >= sizeof(USHORT) && *reinterpret_cast<USHORT*>(raw) == 0;
    WTSFreeMemory(raw);
    if (!WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, id, WTSSessionInfoEx, &raw, &size)) return result;
    if (size >= sizeof(WTSINFOEXW)) {
        auto* info = reinterpret_cast<WTSINFOEXW*>(raw);
        if (info->Level == 1 && info->Data.WTSInfoExLevel1.SessionId == id) {
            const auto& level = info->Data.WTSInfoExLevel1;
            result.logged_on = level.UserName[0] != 0;
            result.username = level.UserName;
            result.domain = level.DomainName;
            result.known = level.SessionFlags == WTS_SESSIONSTATE_LOCK || level.SessionFlags == WTS_SESSIONSTATE_UNLOCK;
            result.locked = level.SessionFlags == WTS_SESSIONSTATE_LOCK;
        }
    }
    WTSFreeMemory(raw);
    if (result.logged_on) result.sid = account_sid(result.domain, result.username);
    return result;
}
std::wstring pipe_name(DWORD session) {
    return L"\\\\.\\pipe\\V8AgentOS.SessionUnlock.v1." + std::to_wstring(session);
}

// Deliberately accepts only this flat six-field JSON contract. No permissive repair,
// unknown fields, duplicate keys, floating point, or unbounded recursive parsing.
Code parse_request(const std::string& json, Request& output) {
    if (json.empty() || json.size() > kMaxJsonBytes) return Code::invalid_request;
    JsonReader reader(json);
    if (!reader.take('{')) return Code::invalid_request;
    std::set<std::wstring> seen;
    for (unsigned i = 0; i < 6; ++i) {
        if (i && !reader.take(',')) return Code::invalid_request;
        std::wstring key;
        if (!reader.text(key) || !seen.insert(key).second || !reader.take(':')) return Code::invalid_request;
        if (key == L"sessionId" || key == L"expiresAt") {
            uint64_t number = 0;
            if (!reader.integer(number)) return Code::invalid_request;
            if (key == L"sessionId") { if (number > MAXDWORD) return Code::invalid_request; output.session_id = static_cast<DWORD>(number); }
            else output.expires_at = number;
            continue;
        }
        wchar_t* target = nullptr;
        size_t capacity = 0;
        if (key == L"username") { target = output.username; capacity = _countof(output.username); }
        else if (key == L"domain") { target = output.domain; capacity = _countof(output.domain); }
        else if (key == L"password") { target = output.password; capacity = _countof(output.password); }
        else if (key == L"requestId") { target = output.request_id; capacity = _countof(output.request_id); }
        else return Code::invalid_request;
        std::wstring value;
        bool parsed = reader.text(value);
        bool fits = parsed && value.size() < capacity;
        if (fits) memcpy(target, value.c_str(), (value.size() + 1) * sizeof(wchar_t));
        if (!value.empty()) SecureZeroMemory(value.data(), value.size() * sizeof(wchar_t));
        if (!fits) return Code::invalid_request;
    }
    return reader.take('}') && reader.end() ? Code::accepted : Code::invalid_request;
}
Code validate_request(const Request& r, uint64_t now) {
    if (r.magic != kMagic || r.version != kProtocol || r.size != sizeof(Request) || !r.session_id ||
        wcsnlen_s(r.username, _countof(r.username)) == 0 || wcsnlen_s(r.username, _countof(r.username)) >= _countof(r.username) ||
        wcsnlen_s(r.domain, _countof(r.domain)) >= _countof(r.domain) ||
        wcsnlen_s(r.password, _countof(r.password)) == 0 || wcsnlen_s(r.password, _countof(r.password)) >= _countof(r.password) ||
        wcsnlen_s(r.request_id, _countof(r.request_id)) != 36) return Code::invalid_request;
    for (size_t i = 0; i < 36; ++i) {
        wchar_t c = r.request_id[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (c != '-') return Code::invalid_request; }
        else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) return Code::invalid_request;
    }
    if (r.expires_at <= now || r.expires_at - now > kMaxLifetimeMs) return Code::expired_request;
    return Code::accepted;
}
Code validate_target(const Request& r, const Session& session) {
    if (r.session_id != session.id) return Code::wrong_session;
    if (!session.known || !session.logged_on || session.sid.empty()) return Code::state_unknown;
    if (!session.local) return Code::remote_session;
    if (session.id != WTSGetActiveConsoleSessionId()) return Code::wrong_session;
    // Never resolve caller-controlled domain names from SYSTEM: that could cause
    // outbound authentication/name lookups. Match the OS-observed account first.
    bool same_name = _wcsicmp(r.username, session.username.c_str()) == 0;
    bool same_domain = !r.domain[0] || _wcsicmp(r.domain, session.domain.c_str()) == 0;
    if (!same_domain && !wcscmp(r.domain, L".")) {
        wchar_t computer[MAX_COMPUTERNAME_LENGTH + 1]{};
        DWORD chars = _countof(computer);
        same_domain = GetComputerNameW(computer, &chars) && _wcsicmp(computer, session.domain.c_str()) == 0;
    }
    if (!same_name || !same_domain) return Code::wrong_user;
    return session.locked ? Code::accepted : Code::already_unlocked;
}
bool verify_pipe_client(HANDLE pipe, const Session& session) {
    if (!ImpersonateNamedPipeClient(pipe)) return false;
    HANDLE raw = nullptr;
    bool valid = false;
    if (OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &raw)) {
        Handle token(raw);
        DWORD id = 0, size = 0;
        valid = token_sid(token.value) == session.sid && GetTokenInformation(token.value, TokenSessionId, &id, sizeof(id), &size) && id == session.id;
    }
    if (!RevertToSelf()) return false;
    ULONG pid = 0;
    DWORD id = 0;
    return valid && GetNamedPipeClientProcessId(pipe, &pid) && ProcessIdToSessionId(pid, &id) && id == session.id;
}
bool verify_pipe_server(HANDLE pipe, DWORD session) {
    ULONG pid = 0;
    if (!GetNamedPipeServerProcessId(pipe, &pid)) return false;
    // Ordinary users may not query a SYSTEM process token. Verify the kernel-owned
    // pipe owner instead, then pin its actual serving PID/image/session. A normal
    // caller cannot create an object owned by SYSTEM.
    PSID owner = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (GetSecurityInfo(pipe, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION, &owner, nullptr, nullptr, nullptr, &descriptor) != ERROR_SUCCESS) return false;
    bool system_owned = IsWellKnownSid(owner, WinLocalSystemSid) != FALSE;
    LocalFree(descriptor);
    if (!system_owned) return false;
    // Opening a SYSTEM process or ProcessIdToSessionId may be denied. WTS
    // supplies process/session identity without opening that process. The
    // SYSTEM-owned pipe and exact session/PID remain the trust boundary; a
    // same-name ordinary-user process cannot manufacture a SYSTEM-owned pipe.
    PWTS_PROCESS_INFOW processes = nullptr;
    DWORD count = 0;
    if (!WTSEnumerateProcessesW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &processes, &count)) return false;
    bool matches = false;
    for (DWORD i = 0; i < count; ++i)
        if (processes[i].ProcessId == pid)
            matches = processes[i].SessionId == session && processes[i].pProcessName && _wcsicmp(processes[i].pProcessName, L"LogonUI.exe") == 0;
    WTSFreeMemory(processes);
    return matches;
}
bool provider_registered() {
    HKEY key = nullptr;
    std::wstring path = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\";
    path += kClsid;
    LONG status = RegOpenKeyExW(HKEY_LOCAL_MACHINE, path.c_str(), 0, KEY_READ | KEY_WOW64_64KEY, &key);
    if (key) RegCloseKey(key);
    return status == ERROR_SUCCESS;
}
Code claim_request(const Request& request) {
    HKEY root = nullptr;
    std::wstring path = std::wstring(kRegistryRoot) + L"\\Replay";
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, path.c_str(), 0, KEY_ALL_ACCESS | KEY_WOW64_64KEY, &root) != ERROR_SUCCESS) return Code::replay_store_unavailable;
    struct Close { HKEY h; ~Close(){RegCloseKey(h);} } close{root};
    // Short-lived non-secret replay tombstones survive LogonUI recreation.
    DWORD remaining = 0;
    for (DWORD i = 0; i < 512;) {
        wchar_t name[64]{};
        DWORD chars = _countof(name);
        LONG status = RegEnumKeyExW(root, i, name, &chars, nullptr, nullptr, nullptr, nullptr);
        if (status == ERROR_NO_MORE_ITEMS) break;
        if (status != ERROR_SUCCESS) return Code::replay_store_unavailable;
        ULONGLONG expires = 0;
        DWORD size = sizeof(expires);
        status = RegGetValueW(root, name, L"ExpiresAt", RRF_RT_REG_QWORD, nullptr, &expires, &size);
        if (status == ERROR_SUCCESS && expires < now_ms()) {
            if (RegDeleteKeyW(root, name) != ERROR_SUCCESS) return Code::replay_store_unavailable;
        } else { ++i; ++remaining; }
    }
    if (remaining >= 256) return Code::busy;
    HKEY item = nullptr;
    DWORD disposition = 0;
    if (RegCreateKeyExW(root, request.request_id, 0, nullptr, 0, KEY_SET_VALUE, nullptr, &item, &disposition) != ERROR_SUCCESS) return Code::replay_store_unavailable;
    Close close_item{item};
    if (disposition != REG_CREATED_NEW_KEY) return Code::replayed_request;
    auto expires = request.expires_at;
    if (RegSetValueExW(item, L"ExpiresAt", 0, REG_QWORD, reinterpret_cast<const BYTE*>(&expires), sizeof(expires)) != ERROR_SUCCESS || RegFlushKey(item) != ERROR_SUCCESS) return Code::replay_store_unavailable;
    return Code::accepted;
}
void print_result(Code code, DWORD session, int submitted, bool installed, bool ready, const Session* observed) {
    const bool ok = code == Code::unlocked || code == Code::already_unlocked || code == Code::locked;
    const char* lock_state = observed && observed->known ? (observed->locked ? "true" : "false") : "null";
    bool verified = code == Code::unlocked || code == Code::already_unlocked;
    std::printf("{\"ok\":%s,\"status\":\"%s\",\"sessionId\":%lu,\"submitted\":%s,\"verified\":%s,\"providerInstalled\":%s,\"registered\":%s,\"available\":%s,\"locked\":%s,\"protocolVersion\":1}\n",
                ok ? "true" : "false", code_name(code), session, submitted < 0 ? "null" : submitted ? "true" : "false", verified ? "true" : "false", installed ? "true" : "false", installed ? "true" : "false", ready ? "true" : "false", lock_state);
}
}  // namespace v8unlock
