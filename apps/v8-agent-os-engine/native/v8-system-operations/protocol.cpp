#include "protocol.h"
#include "../v8-native-common/json_reader.h"
#include <aclapi.h>
#include <algorithm>
#include <sddl.h>
#include <set>
#include <winsvc.h>

namespace v8system {
std::string parse_request(const std::string &json, Request &out) {
    if (json.empty() || json.size() > kMaxRequest)
        return "invalid_request";
    v8native::JsonReader reader(json, kMaxRequest);
    if (!reader.take('{'))
        return "invalid_request";
    std::set<std::wstring> seen;
    for (unsigned i = 0; i < 10; ++i) {
        if (i && !reader.take(','))
            return "invalid_request";
        std::wstring key;
        if (!reader.text(key) || !seen.insert(key).second || !reader.take(':'))
            return "invalid_request";
        if (key == L"argv") {
            if (!reader.take('['))
                return "invalid_argv";
            size_t chars = 0;
            if (reader.take(']'))
                return "invalid_argv";
            do {
                std::wstring arg;
                if (out.argv.size() >= 64 || !reader.text(arg))
                    return "invalid_argv";
                chars += arg.size() + 3;
                if (chars > 32760)
                    return "invalid_argv";
                out.argv.push_back(std::move(arg));
                if (reader.take(']'))
                    break;
                if (!reader.take(','))
                    return "invalid_argv";
            } while (true);
        } else if (key == L"expiresAt" || key == L"timeoutSeconds") {
            uint64_t value = 0;
            if (!reader.integer(value))
                return "invalid_request";
            if (key == L"expiresAt")
                out.expires_at = value;
            else {
                if (value > 600)
                    return "invalid_timeout";
                out.timeout_seconds = static_cast<DWORD>(value);
            }
        } else {
            std::wstring *target = nullptr;
            std::wstring action;
            if (key == L"action")
                target = &action;
            else if (key == L"command")
                target = &out.command;
            else if (key == L"cwd")
                target = &out.cwd;
            else if (key == L"username")
                target = &out.username;
            else if (key == L"domain")
                target = &out.domain;
            else if (key == L"password")
                target = &out.password;
            else if (key == L"requestId")
                target = &out.request_id;
            else
                return "unknown_field";
            if (!reader.text(*target))
                return "invalid_request";
            if (key == L"action" && action != L"run_privileged")
                return "unsupported_action";
        }
    }
    return reader.take('}') && reader.end() ? "" : "invalid_request";
}
static bool full_local_path(const std::wstring &path) {
    return path.size() >= 3 && path.size() < 32768 &&
           ((path[0] >= L'A' && path[0] <= L'Z') || (path[0] >= L'a' && path[0] <= L'z')) &&
           path[1] == L':' && (path[2] == L'\\' || path[2] == L'/') &&
           path.find(L':', 2) == std::wstring::npos;
}
std::string validate_request(const Request &r, uint64_t now) {
    if (r.username.empty() || r.username.size() > 256 || r.password.empty() ||
        r.password.size() > 1024 || r.domain.size() > 256 || r.command.empty() ||
        r.command.size() > 32768)
        return "invalid_request";
    if (r.username.find_first_of(L"\\/@:\r\n") != std::wstring::npos)
        return "local_account_required";
    wchar_t machine[MAX_COMPUTERNAME_LENGTH + 1]{};
    DWORD count = _countof(machine);
    if (!GetComputerNameW(machine, &count))
        return "identity_unavailable";
    if (!r.domain.empty() && r.domain != L"." && _wcsicmp(r.domain.c_str(), machine))
        return "local_account_required";
    if (r.request_id.size() != 36)
        return "invalid_request_id";
    for (size_t i = 0; i < 36; ++i) {
        wchar_t c = r.request_id[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != L'-')
                return "invalid_request_id";
        } else if (!((c >= L'0' && c <= L'9') || (c >= L'a' && c <= L'f') ||
                     (c >= L'A' && c <= L'F')))
            return "invalid_request_id";
    }
    if (r.expires_at <= now || r.expires_at - now > 60000)
        return "expired_request";
    if (r.timeout_seconds < 5 || r.timeout_seconds > 600)
        return "invalid_timeout";
    if (r.argv.empty() || r.argv.size() > 64 || !full_local_path(r.argv[0]) ||
        !full_local_path(r.cwd))
        return "absolute_local_paths_required";
    if (command_line(r.argv).size() >= 32767)
        return "invalid_argv";
    return {};
}
std::wstring quote_argument(const std::wstring &value) {
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : value) {
        if (c == L'\\') {
            ++slashes;
            continue;
        }
        if (c == L'\"') {
            out.append(slashes * 2 + 1, L'\\');
            out += c;
        } else {
            out.append(slashes, L'\\');
            out += c;
        }
        slashes = 0;
    }
    out.append(slashes * 2, L'\\');
    out += L'\"';
    return out;
}
std::wstring command_line(const std::vector<std::wstring> &argv) {
    std::wstring out;
    // cmd /c consumes command text, not a CRT-escaped final argument. Preserve
    // the exact text already approved by Engine, including its nested quotes.
    const size_t slash = argv.empty() ? std::wstring::npos : argv[0].find_last_of(L"\\/");
    const bool cmd = argv.size() == 5 &&
                     !_wcsicmp(argv[0].substr(slash == std::wstring::npos ? 0 : slash + 1).c_str(), L"cmd.exe") &&
                     !_wcsicmp(argv[1].c_str(), L"/d") && !_wcsicmp(argv[2].c_str(), L"/s") && !_wcsicmp(argv[3].c_str(), L"/c");
    if (cmd)
        return quote_argument(argv[0]) + L" /d /s /c " + argv[4];
    for (size_t i = 0; i < argv.size(); ++i) {
        if (!out.empty())
            out += L' ';
        out += quote_argument(argv[i]);
    }
    return out;
}
std::wstring configured_client_sid() {
    wchar_t value[256]{};
    DWORD size = sizeof(value);
    if (RegGetValueW(HKEY_LOCAL_MACHINE, kRegistryRoot, L"ClientSid",
                     RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY, nullptr, value, &size) != ERROR_SUCCESS)
        return {};
    PSID sid = nullptr;
    bool valid = ConvertStringSidToSidW(value, &sid) != FALSE;
    if (sid)
        LocalFree(sid);
    return valid ? value : L"";
}
ServiceInfo service_info() {
    ServiceInfo info;
    SC_HANDLE scm = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (!scm)
        return info;
    SC_HANDLE service =
        OpenServiceW(scm, kServiceName, SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS);
    if (service) {
        DWORD size = 0;
        QueryServiceConfigW(service, nullptr, 0, &size);
        std::vector<BYTE> config(size);
        if (size &&
            QueryServiceConfigW(service, reinterpret_cast<QUERY_SERVICE_CONFIGW *>(config.data()),
                                size, &size)) {
            auto *c = reinterpret_cast<QUERY_SERVICE_CONFIGW *>(config.data());
            info.registered = c->lpServiceStartName &&
                              (!_wcsicmp(c->lpServiceStartName, L"LocalSystem") ||
                               !_wcsicmp(c->lpServiceStartName, L"NT AUTHORITY\\SYSTEM"));
            info.image = c->lpBinaryPathName ? c->lpBinaryPathName : L"";
        }
        SERVICE_STATUS_PROCESS status{};
        if (QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO, reinterpret_cast<BYTE *>(&status),
                                 sizeof(status), &size)) {
            info.running = status.dwCurrentState == SERVICE_RUNNING;
            info.pid = status.dwProcessId;
        }
        CloseServiceHandle(service);
    }
    CloseServiceHandle(scm);
    return info;
}
bool verify_server(HANDLE pipe, const ServiceInfo &info) {
    ULONG pid = 0;
    if (!info.registered || !info.running || !info.pid ||
        !GetNamedPipeServerProcessId(pipe, &pid) || pid != info.pid)
        return false;
    PSID owner = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (GetSecurityInfo(pipe, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION, &owner, nullptr,
                        nullptr, nullptr, &descriptor) != ERROR_SUCCESS)
        return false;
    bool owned = IsWellKnownSid(owner, WinLocalSystemSid) != FALSE;
    LocalFree(descriptor);
    if (!owned)
        return false;
    // SCM is the authority for this running service's PID and image. An
    // unelevated caller may be unable to open the SYSTEM process even for a
    // limited image query; do not make that unrelated permission a prerequisite.
    wchar_t installed[32768]{};
    DWORD bytes = sizeof(installed);
    if (RegGetValueW(HKEY_LOCAL_MACHINE, kRegistryRoot, L"ClientPath",
                     RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY, nullptr, installed, &bytes) != ERROR_SUCCESS || !installed[0])
        return false;
    const auto current = service_info();
    return current.running && current.pid == pid &&
           _wcsicmp(info.image.c_str(), (quote_argument(installed) + L" --service").c_str()) == 0 &&
           current.image == info.image;
}
bool verify_client(HANDLE pipe, const std::wstring &expected) {
    if (expected.empty() || !ImpersonateNamedPipeClient(pipe))
        return false;
    HANDLE raw = nullptr;
    bool allowed = false;
    if (OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &raw)) {
        Handle token(raw);
        allowed = v8native::token_sid(token.value) == expected;
    }
    return RevertToSelf() && allowed;
}
bool elevated_administrator(HANDLE token) {
    TOKEN_ELEVATION elevation{};
    DWORD size = 0;
    TOKEN_TYPE type{};
    if (!GetTokenInformation(token, TokenType, &type, sizeof(type), &size) ||
        type != TokenPrimary ||
        !GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size) ||
        !elevation.TokenIsElevated)
        return false;
    auto sid = v8native::token_sid(token);
    if (sid.empty() || sid == L"S-1-5-18" || sid == L"S-1-5-19" || sid == L"S-1-5-20")
        return false;
    GetTokenInformation(token, TokenGroups, nullptr, 0, &size);
    if (!size || size > 65536)
        return false;
    std::vector<BYTE> bytes(size);
    if (!GetTokenInformation(token, TokenGroups, bytes.data(), size, &size))
        return false;
    auto *groups = reinterpret_cast<TOKEN_GROUPS *>(bytes.data());
    bool admin = false;
    for (DWORD i = 0; i < groups->GroupCount; ++i)
        if (IsWellKnownSid(groups->Groups[i].Sid, WinBuiltinAdministratorsSid) &&
            (groups->Groups[i].Attributes & SE_GROUP_ENABLED) &&
            !(groups->Groups[i].Attributes & SE_GROUP_USE_FOR_DENY_ONLY))
            admin = true;
    GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &size);
    if (!size || size > 65536)
        return false;
    bytes.resize(size);
    if (!GetTokenInformation(token, TokenIntegrityLevel, bytes.data(), size, &size))
        return false;
    PSID integrity = reinterpret_cast<TOKEN_MANDATORY_LABEL *>(bytes.data())->Label.Sid;
    DWORD level = *GetSidSubAuthority(integrity, *GetSidSubAuthorityCount(integrity) - 1);
    return admin && level >= SECURITY_MANDATORY_HIGH_RID;
}
std::string claim_request(const Request &r) {
    HKEY root = nullptr;
    std::wstring path = std::wstring(kRegistryRoot) + L"\\Replay";
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, path.c_str(), 0, KEY_ALL_ACCESS | KEY_WOW64_64KEY,
                      &root) != ERROR_SUCCESS)
        return "replay_store_unavailable";
    struct Close {
        HKEY h;
        ~Close() {
            RegCloseKey(h);
        }
    } close{root};
    DWORD alive = 0;
    for (DWORD i = 0; i < 256;) {
        wchar_t name[64]{};
        DWORD count = _countof(name);
        LONG status = RegEnumKeyExW(root, i, name, &count, nullptr, nullptr, nullptr, nullptr);
        if (status == ERROR_NO_MORE_ITEMS)
            break;
        if (status != ERROR_SUCCESS)
            return "replay_store_unavailable";
        ULONGLONG expires = 0;
        DWORD size = sizeof(expires);
        status = RegGetValueW(root, name, L"ExpiresAt", RRF_RT_REG_QWORD, nullptr, &expires, &size);
        if (status == ERROR_SUCCESS && expires < now_ms()) {
            if (RegDeleteKeyW(root, name) != ERROR_SUCCESS)
                return "replay_store_unavailable";
        } else {
            ++i;
            ++alive;
        }
    }
    if (alive >= 256)
        return "busy";
    HKEY item = nullptr;
    DWORD disposition = 0;
    if (RegCreateKeyExW(root, r.request_id.c_str(), 0, nullptr, 0, KEY_SET_VALUE, nullptr, &item,
                        &disposition) != ERROR_SUCCESS)
        return "replay_store_unavailable";
    Close close_item{item};
    if (disposition != REG_CREATED_NEW_KEY)
        return "replayed_request";
    auto expires = r.expires_at;
    if (RegSetValueExW(item, L"ExpiresAt", 0, REG_QWORD, reinterpret_cast<const BYTE *>(&expires),
                       sizeof(expires)) != ERROR_SUCCESS ||
        RegFlushKey(item) != ERROR_SUCCESS)
        return "replay_store_unavailable";
    return {};
}
std::string json_text(const std::string &value) {
    // Normalize invalid UTF-8 for the human-readable projection; raw byte totals
    // and truncation remain explicit. Never include request/exception text here.
    int count =
        MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    std::wstring wide(count, L'\0');
    if (count)
        MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), wide.data(),
                            count);
    int bytes = WideCharToMultiByte(CP_UTF8, 0, wide.data(), count, nullptr, 0, nullptr, nullptr);
    std::string normalized(bytes, '\0');
    if (bytes)
        WideCharToMultiByte(CP_UTF8, 0, wide.data(), count, normalized.data(), bytes, nullptr,
                            nullptr);
    std::string out = "\"";
    const char hex[] = "0123456789abcdef";
    for (unsigned char c : normalized) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += static_cast<char>(c);
        } else if (c < 32) {
            out += "\\u00";
            out += hex[c >> 4];
            out += hex[c & 15];
        } else
            out += static_cast<char>(c);
    }
    out += '"';
    return out;
}
std::string result(const std::string &code, bool executed, bool elevated, bool verified,
                   DWORD exit_code, bool has_exit, const std::string &out, const std::string &err,
                   bool truncated, bool tree_stopped, bool execution_known) {
    return "{\"ok\":" +
           std::string(code == "completed" && execution_known && executed && elevated && verified &&
                               has_exit && exit_code == 0 && tree_stopped
                           ? "true"
                           : "false") +
           ",\"code\":" + json_text(code) + ",\"status\":" + json_text(code) +
           ",\"executed\":" + (execution_known ? (executed ? "true" : "false") : "null") +
           ",\"elevated\":" + (elevated ? "true" : "false") +
           ",\"verified\":" + (verified ? "true" : "false") +
           ",\"exitCode\":" + (has_exit ? std::to_string(exit_code) : "null") +
           ",\"stdout\":" + json_text(out) + ",\"stderr\":" + json_text(err) +
           ",\"outputTruncated\":" + (truncated ? "true" : "false") +
           ",\"outputEncoding\":\"utf-8-replacement\",\"processTreeStopped\":" +
           (tree_stopped ? "true" : "false") + ",\"protocolVersion\":1}";
}
bool send_frame(HANDLE pipe, const std::string &data, uint64_t deadline, HANDLE stop,
                DWORD max_wait) {
    Frame frame;
    frame.bytes = static_cast<DWORD>(data.size());
    return v8native::pipe_io(pipe, &frame, sizeof(frame), true, deadline, stop, max_wait) &&
           v8native::pipe_io(pipe, const_cast<char *>(data.data()), frame.bytes, true, deadline,
                             stop, max_wait);
}
bool receive_frame(HANDLE pipe, std::string &data, size_t limit, uint64_t deadline, HANDLE stop,
                   DWORD max_wait) {
    Frame frame;
    if (!v8native::pipe_io(pipe, &frame, sizeof(frame), false, deadline, stop, max_wait) ||
        frame.magic != kMagic || frame.version != 1 || !frame.bytes || frame.bytes > limit)
        return false;
    data.resize(frame.bytes);
    return v8native::pipe_io(pipe, data.data(), frame.bytes, false, deadline, stop, max_wait);
}
} // namespace v8system
