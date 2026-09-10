#include "protocol.h"
#include <algorithm>
#include <cstdio>
#include <fcntl.h>
#include <io.h>
#include <userenv.h>

namespace v8system {
static bool privilege(const wchar_t *name) {
    HANDLE raw = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &raw))
        return false;
    Handle token(raw);
    TOKEN_PRIVILEGES privileges{};
    privileges.PrivilegeCount = 1;
    if (!LookupPrivilegeValueW(nullptr, name, &privileges.Privileges[0].Luid))
        return false;
    privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    return AdjustTokenPrivileges(token.value, FALSE, &privileges, 0, nullptr, nullptr) &&
           GetLastError() == ERROR_SUCCESS;
}
static std::string authenticate(Request &request, Handle &primary) {
    if (!privilege(SE_TCB_NAME) || !privilege(SE_ASSIGNPRIMARYTOKEN_NAME) ||
        !privilege(SE_INCREASE_QUOTA_NAME))
        return "service_privileges_unavailable";
    HANDLE raw = nullptr;
    BOOL logged_on = LogonUserW(request.username.c_str(), L".", request.password.c_str(),
                                LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT, &raw);
    DWORD error = logged_on ? ERROR_SUCCESS : GetLastError();
    request.clear_password(); // never inherited by the target program
    if (!logged_on)
        return error == ERROR_PASSWORD_EXPIRED || error == ERROR_PASSWORD_MUST_CHANGE
                   ? "password_expired"
               : error == ERROR_ACCOUNT_RESTRICTION || error == ERROR_ACCOUNT_DISABLED ||
                       error == ERROR_ACCOUNT_LOCKED_OUT
                   ? "account_restricted"
                   : "authentication_failed";
    Handle initial(raw), linked;
    HANDLE candidate = initial.value;
    if (!elevated_administrator(candidate)) {
        TOKEN_ELEVATION_TYPE kind{};
        DWORD size = 0;
        if (!GetTokenInformation(candidate, TokenElevationType, &kind, sizeof(kind), &size) ||
            kind != TokenElevationTypeLimited)
            return "administrator_token_required";
        TOKEN_LINKED_TOKEN pair{};
        if (!GetTokenInformation(candidate, TokenLinkedToken, &pair, sizeof(pair), &size))
            return "elevated_token_unavailable";
        linked.reset(pair.LinkedToken);
        candidate = linked.value;
    }
    if (!DuplicateTokenEx(candidate,
                          TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY |
                              TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_SESSIONID,
                          nullptr, SecurityImpersonation, TokenPrimary, &raw))
        return "primary_token_unavailable";
    primary.reset(raw);
    if (!elevated_administrator(primary.value) ||
        v8native::token_sid(primary.value) != v8native::token_sid(initial.value))
        return "administrator_token_required";
    DWORD session = 0; // background command, never the user's interactive desktop
    if (!SetTokenInformation(primary.value, TokenSessionId, &session, sizeof(session)))
        return "background_session_unavailable";
    return {};
}
static bool make_pipe(Handle &read, Handle &write) {
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    HANDLE r = nullptr, w = nullptr;
    if (!CreatePipe(&r, &w, &security, 65536))
        return false;
    read.reset(r);
    write.reset(w);
    return SetHandleInformation(read.value, HANDLE_FLAG_INHERIT, 0) != FALSE;
}
static void drain(HANDLE pipe, std::string &captured, bool &truncated) {
    for (unsigned i = 0; i < 16; ++i) {
        DWORD available = 0;
        if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr) || !available)
            return;
        char buffer[4096];
        DWORD read = 0;
        if (!ReadFile(pipe, buffer, std::min<DWORD>(available, sizeof(buffer)), &read, nullptr) ||
            !read)
            return;
        size_t keep = std::min<size_t>(read, kMaxOutput - captured.size());
        captured.append(buffer, keep);
        truncated |= keep != read;
    }
}
static std::vector<wchar_t> environment(HANDLE token) {
    wchar_t windows[MAX_PATH]{};
    if (!GetWindowsDirectoryW(windows, _countof(windows)))
        return {};
    DWORD count = 0;
    GetUserProfileDirectoryW(token, nullptr, &count);
    std::vector<wchar_t> profile(count + 1);
    if (!count || count > 32768 || !GetUserProfileDirectoryW(token, profile.data(), &count))
        return {};
    std::wstring root(windows), home(profile.data());
    // Deliberately do not inherit the SYSTEM service/Engine environment. The
    // credential and internal IPC handle are not application inputs.
    std::vector<std::wstring> entries{L"SystemRoot=" + root,
                                      L"WINDIR=" + root,
                                      L"PATH=" + root + L"\\System32;" + root + L";" + root +
                                          L"\\System32\\WindowsPowerShell\\v1.0",
                                      L"TEMP=" + home + L"\\AppData\\Local\\Temp",
                                      L"TMP=" + home + L"\\AppData\\Local\\Temp",
                                      L"USERPROFILE=" + home,
                                      L"COMSPEC=" + root + L"\\System32\\cmd.exe"};
    std::sort(entries.begin(), entries.end(),
              [](const auto &a, const auto &b) { return _wcsicmp(a.c_str(), b.c_str()) < 0; });
    std::vector<wchar_t> block;
    for (const auto &item : entries) {
        block.insert(block.end(), item.begin(), item.end());
        block.push_back(0);
    }
    block.push_back(0);
    return block;
}
std::string worker_execute(Request &request) {
    std::string failure = validate_request(request, now_ms());
    if (!failure.empty())
        return result(failure);
    if (v8native::process_sid(GetCurrentProcessId()) != L"S-1-5-18")
        return result("service_identity_required");
    Handle token;
    failure = authenticate(request, token);
    if (!failure.empty())
        return result(failure);
    if (now_ms() >= request.expires_at)
        return result("expired_request");
    // This fixed phase is private to the service worker protocol, not a completion.
    std::fputs("V8_AUTHENTICATED\n", stdout);
    std::fflush(stdout);
    // Filesystem probes use the authenticated administrator's token, not the
    // broker's SYSTEM authority. This remains a process broker, not an FS sandbox.
    if (!ImpersonateLoggedOnUser(token.value))
        return result("target_access_unavailable");
    DWORD attributes = GetFileAttributesW(request.cwd.c_str());
    bool cwd_ok = attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY);
    attributes = GetFileAttributesW(request.argv[0].c_str());
    bool executable_ok =
        attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
    bool remote = false;
    for (const auto &path : {request.cwd, request.argv[0]})
        remote |= GetDriveTypeW(path.substr(0, 3).c_str()) == DRIVE_REMOTE;
    if (!RevertToSelf())
        return result("service_identity_unavailable");
    if (!cwd_ok)
        return result("cwd_unavailable");
    if (!executable_ok)
        return result("executable_unavailable");
    if (remote)
        return result("remote_path_rejected");
    auto env = environment(token.value);
    if (env.empty())
        return result("user_environment_unavailable");
    Handle out_read, out_write, err_read, err_write;
    if (!make_pipe(out_read, out_write) || !make_pipe(err_read, err_write))
        return result("output_pipe_unavailable");
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    Handle input(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &security,
                             OPEN_EXISTING, 0, nullptr));
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!input.valid() || !job.valid() ||
        !SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits,
                                 sizeof(limits)))
        return result("job_unavailable");
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.StartupInfo.wShowWindow = SW_HIDE;
    startup.StartupInfo.hStdInput = input.value;
    startup.StartupInfo.hStdOutput = out_write.value;
    startup.StartupInfo.hStdError = err_write.value;
    SIZE_T size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
    std::vector<BYTE> attribute_list(size);
    startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attribute_list.data());
    if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &size))
        return result("handle_list_unavailable");
    struct Attributes {
        LPPROC_THREAD_ATTRIBUTE_LIST value;
        ~Attributes() {
            DeleteProcThreadAttributeList(value);
        }
    } cleanup{startup.lpAttributeList};
    HANDLE inherited[]{input.value, out_write.value, err_write.value};
    if (!UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inherited, sizeof(inherited), nullptr, nullptr))
        return result("handle_list_unavailable");
    auto line = command_line(request.argv);
    PROCESS_INFORMATION process{};
    if (!CreateProcessAsUserW(token.value, request.argv[0].c_str(), line.data(), nullptr, nullptr,
                              TRUE,
                              CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT |
                                  EXTENDED_STARTUPINFO_PRESENT,
                              env.data(), request.cwd.c_str(), &startup.StartupInfo, &process))
        return result("process_create_failed");
    Handle child(process.hProcess), thread(process.hThread);
    if (!AssignProcessToJobObject(job.value, child.value)) {
        TerminateProcess(child.value, 125);
        WaitForSingleObject(child.value, 5000);
        return result("job_assignment_failed");
    }
    HANDLE raw = nullptr;
    if (!OpenProcessToken(child.value, TOKEN_QUERY, &raw)) {
        TerminateJobObject(job.value, 125);
        return result("child_token_unverified");
    }
    Handle actual(raw);
    if (!elevated_administrator(actual.value) ||
        v8native::token_sid(actual.value) != v8native::token_sid(token.value)) {
        TerminateJobObject(job.value, 125);
        return result("child_token_unverified");
    }
    out_write.reset();
    err_write.reset();
    input.reset();
    if (now_ms() >= request.expires_at) {
        TerminateJobObject(job.value, 125);
        return result("expired_request");
    }
    if (ResumeThread(thread.value) == static_cast<DWORD>(-1)) {
        TerminateJobObject(job.value, 125);
        return result("process_start_failed");
    }
    std::string out, err;
    bool truncated = false, timed_out = false;
    ULONGLONG deadline = GetTickCount64() + static_cast<ULONGLONG>(request.timeout_seconds) * 1000;
    while (WaitForSingleObject(child.value, 0) == WAIT_TIMEOUT) {
        drain(out_read.value, out, truncated);
        drain(err_read.value, err, truncated);
        if (GetTickCount64() >= deadline) {
            timed_out = true;
            TerminateJobObject(job.value, 124);
            break;
        }
        Sleep(20);
    }
    bool root_stopped = WaitForSingleObject(child.value, 5000) == WAIT_OBJECT_0;
    DWORD exit_code = 0;
    bool got_exit = root_stopped && GetExitCodeProcess(child.value, &exit_code);
    // Stop descendants even when the root command exited successfully. This is
    // a bounded command operation, not a detached service launcher.
    TerminateJobObject(job.value, timed_out ? 124 : 0);
    bool stopped = v8native::job_is_empty(job.value);
    drain(out_read.value, out, truncated);
    drain(err_read.value, err, truncated);
    return result(timed_out               ? "timed_out"
                  : !got_exit || !stopped ? "outcome_unknown"
                  : exit_code             ? "process_failed"
                                          : "completed",
                  true, true, got_exit && stopped && !timed_out, exit_code, got_exit, out, err,
                  truncated, stopped);
}
int worker_entry() {
    if (v8native::process_sid(GetCurrentProcessId()) != L"S-1-5-18") {
        std::puts(result("service_identity_required").c_str());
        return 2;
    }
    _setmode(_fileno(stdin), _O_BINARY);
    std::string json;
    json.reserve(kMaxRequest + 1);
    struct Wipe {
        std::string &s;
        ~Wipe() {
            if (!s.empty())
                SecureZeroMemory(s.data(), s.size());
        }
    } wipe{json};
    for (size_t i = 0; i <= kMaxRequest; ++i) {
        int c = std::getchar();
        if (c == EOF)
            break;
        json.push_back(static_cast<char>(c));
    }
    Request request;
    auto failure = parse_request(json, request);
    if (!json.empty())
        SecureZeroMemory(json.data(), json.size());
    std::string response = failure.empty() ? worker_execute(request) : result(failure);
    std::puts(response.c_str());
    return 0;
}
} // namespace v8system
