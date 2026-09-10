#include "protocol.h"
#include <cstdio>
#include <cstdlib>
#include <shellapi.h>
#include <thread>
using namespace v8system;
static unsigned checks = 0;
static void check(bool value) {
    ++checks;
    if (!value) {
        std::fprintf(stderr, "protocol assertion %u failed\n", checks);
        std::exit(1);
    }
}
static std::string fixture() {
    return "{\"action\":\"run_privileged\",\"command\":\"fixture\",\"argv\":[\"C:"
           "\\\\Windows\\\\System32\\\\whoami.exe\",\"/"
           "groups\"],\"cwd\":\"C:\\\\Windows\",\"timeoutSeconds\":5,\"username\":\"fixture-user\","
           "\"domain\":\".\",\"password\":\"fixture-only\",\"requestId\":\"24363516-7eaa-4a3d-90a4-"
           "093b47abebf4\",\"expiresAt\":150000}";
}
static void framed_response_checks() {
    // Exercise the real frame transport with a response larger than a single
    // status record, without authenticating or connecting to the installed service.
    const std::string response = result("fixture_reply", false, false, false, 0, false,
                                        std::string(32768, 'x') + "\n尾部证据");
    for (unsigned mode = 0; mode < 5; ++mode) {
        const auto name = L"\\\\.\\pipe\\V8SystemResponseTest." +
            std::to_wstring(GetCurrentProcessId()) + L"." + std::to_wstring(mode);
        Handle server(CreateNamedPipeW(name.c_str(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
            1, 65536, 4096, 1000, nullptr));
        check(server.valid());
        Handle peer(CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
            OPEN_EXISTING, FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION, nullptr));
        check(peer.valid());
        DWORD read_mode = PIPE_READMODE_MESSAGE;
        check(SetNamedPipeHandleState(peer.value, &read_mode, nullptr, nullptr) != FALSE);
        if (mode == 0) {
            ULONG pid = 0;
            check(GetNamedPipeServerProcessId(peer.value, &pid) && pid == GetCurrentProcessId());
            wchar_t image[32768]{};
            check(GetModuleFileNameW(nullptr, image, _countof(image)) != 0);
            ServiceInfo spoof;
            spoof.registered = true; spoof.running = true; spoof.pid = pid;
            spoof.image = quote_argument(image) + L" --service";
            check(!verify_server(peer.value, spoof));
            // Sensitivity control: complete server-side writes followed by an
            // immediate disconnect lose the pending frame before client receipt.
            check(send_frame(server.value, response, now_ms() + 1000));
            check(DisconnectNamedPipe(server.value) != FALSE);
            std::string received;
            check(!receive_frame(peer.value, received, kMaxReply, now_ms() + 1000));
            continue;
        }
        Handle sent(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        Handle stop(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        check(sent.valid() && stop.valid());
        bool written = false, receipt = false;
        ULONGLONG elapsed = 0;
        std::thread serving([&] {
            written = send_frame(server.value, response, now_ms() + 1000);
            SetEvent(sent.value);
            const auto start = GetTickCount64();
            BYTE ack = 0;
            receipt = written && v8native::pipe_io(server.value, &ack, 1, false, now_ms() + 750, stop.value) && ack == 6;
            elapsed = GetTickCount64() - start;
            DisconnectNamedPipe(server.value);
        });
        check(WaitForSingleObject(sent.value, 2000) == WAIT_OBJECT_0);
        if (mode == 1) {
            std::string received;
            check(receive_frame(peer.value, received, kMaxReply, now_ms() + 1000));
            check(received == response);
            BYTE ack = 6;
            check(v8native::pipe_io(peer.value, &ack, 1, true, now_ms() + 1000));
        } else if (mode == 2) peer.reset();
        else if (mode == 4) SetEvent(stop.value);
        // mode 3 leaves the client connected but never reads/acknowledges.
        serving.join();
        check(written && receipt == (mode == 1));
        check(elapsed < 2000);
    }
}
int wmain(int argc, wchar_t **args) {
    if (argc == 3 && !wcscmp(args[1], L"--quoted-argument"))
        return wcscmp(args[2], L"two words 中文") == 0 ? 0 : 23;
    if (argc == 2 &&
        (!wcscmp(args[1], L"--owned-job-parent") || !wcscmp(args[1], L"--owned-job-leaf"))) {
        if (!wcscmp(args[1], L"--owned-job-parent")) {
            wchar_t exe[32768]{};
            GetModuleFileNameW(nullptr, exe, _countof(exe));
            std::wstring line = quote_argument(exe) + L" --owned-job-leaf";
            STARTUPINFOW startup{};
            startup.cb = sizeof(startup);
            PROCESS_INFORMATION child{};
            if (CreateProcessW(exe, line.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                               nullptr, &startup, &child)) {
                CloseHandle(child.hThread);
                CloseHandle(child.hProcess);
            }
        }
        Sleep(6000);
        return 0;
    }
    Request request;
    check(parse_request(fixture(), request).empty());
    check(validate_request(request, 100000).empty());
    check(validate_request(request, 150000) == "expired_request");
    check(validate_request(request, 89999) == "expired_request");
    request.timeout_seconds = 601;
    check(validate_request(request, 100000) == "invalid_timeout");
    request.timeout_seconds = 5;
    request.domain = L"\\\\remote-host";
    check(validate_request(request, 100000) == "local_account_required");
    request.domain = L".";
    request.username = L"NT AUTHORITY\\SYSTEM";
    check(validate_request(request, 100000) == "local_account_required");
    request.username = L"fixture-user";
    request.argv[0] = L"whoami.exe";
    check(validate_request(request, 100000) == "absolute_local_paths_required");
    for (auto json : {fixture().substr(0, fixture().size() - 1), fixture() + "[]"}) {
        Request invalid;
        check(!parse_request(json, invalid).empty());
    }
    auto duplicate = fixture();
    duplicate.replace(duplicate.find("command"), 7, "username");
    {
        Request invalid;
        check(!parse_request(duplicate, invalid).empty());
    }
    auto unknown = fixture();
    unknown.replace(unknown.find("command"), 7, "unknown");
    {
        Request invalid;
        check(parse_request(unknown, invalid) == "unknown_field");
    }
    auto empty = fixture();
    empty.replace(empty.find("/groups"), 7, "");
    {
        Request with_empty;
        check(parse_request(empty, with_empty).empty());
        check(with_empty.argv[1].empty());
    }
    std::vector<std::wstring> argv{
        L"C:\\Program Files\\owned.exe", L"", L"a b", L"quote\"here", L"trailing\\", L"你好"};
    int count = 0;
    LPWSTR *decoded = CommandLineToArgvW(command_line(argv).c_str(), &count);
    check(decoded && count == static_cast<int>(argv.size()));
    for (size_t i = 0; i < argv.size(); ++i)
        check(argv[i] == decoded[i]);
    LocalFree(decoded);
    {
        wchar_t self[32768]{}, system[MAX_PATH]{};
        check(GetModuleFileNameW(nullptr, self, _countof(self)) != 0);
        check(GetSystemDirectoryW(system, _countof(system)) != 0);
        const std::wstring cmd = std::wstring(system) + L"\\cmd.exe";
        const std::wstring text = L"\"\"" + std::wstring(self) + L"\" --quoted-argument \"two words 中文\"\"";
        auto line = command_line({cmd, L"/d", L"/s", L"/c", text});
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION child{};
        const bool started = CreateProcessW(cmd.c_str(), line.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &child) != FALSE;
        check(started);
        if (started) {
            Handle process(child.hProcess), thread(child.hThread);
            check(WaitForSingleObject(process.value, 5000) == WAIT_OBJECT_0);
            DWORD exit_code = 99;
            check(GetExitCodeProcess(process.value, &exit_code) && exit_code == 0);
        }
    }
    check(!elevated_administrator(INVALID_HANDLE_VALUE));
    check(!verify_server(INVALID_HANDLE_VALUE, {}));
    check(!verify_client(INVALID_HANDLE_VALUE, L"S-1-5-21-1"));
    check(!v8native::job_is_empty(INVALID_HANDLE_VALUE, 0));
    HANDLE current_raw = nullptr;
    check(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, &current_raw) !=
          FALSE);
    Handle current_token(current_raw);
    BYTE admin_buffer[SECURITY_MAX_SID_SIZE]{};
    DWORD admin_size = sizeof(admin_buffer);
    check(CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr, admin_buffer, &admin_size) !=
          FALSE);
    SID_AND_ATTRIBUTES disabled{admin_buffer, 0};
    HANDLE restricted_raw = nullptr;
    check(CreateRestrictedToken(current_token.value, DISABLE_MAX_PRIVILEGE, 1, &disabled, 0,
                                nullptr, 0, nullptr, &restricted_raw) != FALSE);
    Handle restricted(restricted_raw);
    check(!elevated_administrator(restricted.value));
    std::wstring pipe_name =
        L"\\\\.\\pipe\\V8SystemOperationsTest." + std::to_wstring(GetCurrentProcessId());
    Handle server(
        CreateNamedPipeW(pipe_name.c_str(),
                         PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                         PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS, 1,
                         1024, 1024, 1000, nullptr));
    check(server.valid());
    Handle peer(CreateFileW(pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                            OPEN_EXISTING, SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION,
                            nullptr));
    check(peer.valid());
    char marker = 'x', observed = 0;
    DWORD written = 0;
    check(WriteFile(peer.value, &marker, 1, &written, nullptr) && written == 1);
    check(v8native::pipe_io(server.value, &observed, 1, false, now_ms() + 1000) &&
          observed == marker);
    check(verify_client(server.value, v8native::token_sid(current_token.value)));
    check(!verify_client(server.value, L"S-1-5-21-1"));
    ServiceInfo spoof;
    spoof.registered = true;
    spoof.running = true;
    spoof.pid = GetCurrentProcessId();
    check(!verify_server(peer.value, spoof));
    Frame invalid_frame;
    invalid_frame.version = 2;
    invalid_frame.bytes = 1;
    check(WriteFile(peer.value, &invalid_frame, sizeof(invalid_frame), &written, nullptr) != FALSE);
    std::string frame_body;
    check(!receive_frame(server.value, frame_body, kMaxRequest, now_ms() + 1000));
    DisconnectNamedPipe(server.value);
    // Real, unelevated owned processes prove the shared job completion check.
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    check(job.valid() && SetInformationJobObject(job.value, JobObjectExtendedLimitInformation,
                                                 &limits, sizeof(limits)));
    wchar_t exe[32768]{};
    GetModuleFileNameW(nullptr, exe, _countof(exe));
    std::wstring line = quote_argument(exe) + L" --owned-job-parent";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION owned{};
    check(CreateProcessW(exe, line.data(), nullptr, nullptr, FALSE,
                         CREATE_SUSPENDED | CREATE_NO_WINDOW, nullptr, nullptr, &startup,
                         &owned) != FALSE);
    Handle owned_process(owned.hProcess), owned_thread(owned.hThread);
    check(AssignProcessToJobObject(job.value, owned_process.value) != FALSE);
    check(ResumeThread(owned_thread.value) != static_cast<DWORD>(-1));
    bool descendants = false;
    for (unsigned i = 0; i < 150 && !descendants; ++i) {
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION state{};
        if (QueryInformationJobObject(job.value, JobObjectBasicAccountingInformation, &state,
                                      sizeof(state), nullptr))
            descendants = state.ActiveProcesses >= 2;
        if (!descendants)
            Sleep(20);
    }
    check(descendants);
    check(!v8native::job_is_empty(job.value, 0));
    check(TerminateJobObject(job.value, 123) != FALSE);
    check(v8native::job_is_empty(job.value));
    // Job accounting can reach zero a moment before the process object signals.
    check(WaitForSingleObject(owned_process.value, 5000) == WAIT_OBJECT_0);
    check(result("authentication_failed").find("\"ok\":false") != std::string::npos);
    check(result("completed", true, false, false, 0, true).find("\"ok\":false") !=
          std::string::npos);
    check(
        result("completed", true, false, true, 0, true, {}, {}, false, true).find("\"ok\":false") !=
        std::string::npos);
    check(
        result("completed", false, true, true, 0, true, {}, {}, false, true).find("\"ok\":false") !=
        std::string::npos);
    check(
        result("completed", true, true, true, 0, true, {}, {}, false, false).find("\"ok\":false") !=
        std::string::npos);
    check(result("completed", true, true, true, 0, true, {}, {}, false, true).find("\"ok\":true") !=
          std::string::npos);
    check(result("outcome_unknown", false, false, false, 0, false, {}, {}, false, false, false)
              .find("\"executed\":null") != std::string::npos);
    check(json_text("a\n\"b") == "\"a\\u000a\\\"b\"");
    framed_response_checks();
    std::printf("%u checks passed; no service installation, authentication or elevated process "
                "attempted.\n",
                checks);
    return 0;
}
