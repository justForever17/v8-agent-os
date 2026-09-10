#include "protocol.h"
#include <algorithm>
#include <sddl.h>
#include <thread>
#include <winsvc.h>

namespace v8system {
static SERVICE_STATUS_HANDLE service_handle = nullptr;
static SERVICE_STATUS service_status{};
static Handle stopping;
static void report(DWORD state, DWORD error = 0) {
    service_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    service_status.dwCurrentState = state;
    service_status.dwWin32ExitCode = error;
    service_status.dwControlsAccepted =
        state == SERVICE_RUNNING ? SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN : 0;
    service_status.dwWaitHint = state == SERVICE_STOP_PENDING ? 5000 : 0;
    if (service_handle)
        SetServiceStatus(service_handle, &service_status);
}
static DWORD WINAPI control(DWORD event, DWORD, void *, void *) {
    if (event == SERVICE_CONTROL_STOP || event == SERVICE_CONTROL_SHUTDOWN) {
        report(SERVICE_STOP_PENDING);
        if (stopping.valid())
            SetEvent(stopping.value);
    }
    return NO_ERROR;
}
static bool anonymous_pipe(Handle &read, Handle &write, bool inherit_read) {
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    HANDLE r = nullptr, w = nullptr;
    if (!CreatePipe(&r, &w, &security, 65536))
        return false;
    read.reset(r);
    write.reset(w);
    return SetHandleInformation(inherit_read ? write.value : read.value, HANDLE_FLAG_INHERIT, 0) !=
           FALSE;
}
static std::string supervise_worker(HANDLE client, std::string &json, DWORD timeout) {
    Handle input_read, input_write, output_read, output_write;
    if (!anonymous_pipe(input_read, input_write, true) ||
        !anonymous_pipe(output_read, output_write, false))
        return result("worker_pipe_unavailable");
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    Handle errors(CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &security,
                              OPEN_EXISTING, 0, nullptr));
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!errors.valid() || !job.valid() ||
        !SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits,
                                 sizeof(limits)))
        return result("worker_job_unavailable");
    wchar_t executable[32768]{};
    DWORD length = GetModuleFileNameW(nullptr, executable, _countof(executable));
    if (!length || length >= _countof(executable))
        return result("service_image_unavailable");
    std::wstring line = quote_argument(executable) + L" --worker";
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.StartupInfo.wShowWindow = SW_HIDE;
    startup.StartupInfo.hStdInput = input_read.value;
    startup.StartupInfo.hStdOutput = output_write.value;
    startup.StartupInfo.hStdError = errors.value;
    SIZE_T bytes = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
    std::vector<BYTE> buffer(bytes);
    startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(buffer.data());
    if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &bytes))
        return result("worker_handle_list_unavailable");
    struct Attributes {
        LPPROC_THREAD_ATTRIBUTE_LIST p;
        ~Attributes() {
            DeleteProcThreadAttributeList(p);
        }
    } attributes{startup.lpAttributeList};
    HANDLE inherited[]{input_read.value, output_write.value, errors.value};
    if (!UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inherited, sizeof(inherited), nullptr, nullptr))
        return result("worker_handle_list_unavailable");
    PROCESS_INFORMATION created{};
    // Only this fixed, installed implementation runs as SYSTEM. User argv is not
    // part of its command line and cannot select a different SYSTEM executable.
    if (!CreateProcessW(executable, line.data(), nullptr, nullptr, TRUE,
                        CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, nullptr,
                        nullptr, &startup.StartupInfo, &created))
        return result("worker_create_failed");
    Handle process(created.hProcess), thread(created.hThread);
    if (!AssignProcessToJobObject(job.value, process.value)) {
        TerminateProcess(process.value, 125);
        WaitForSingleObject(process.value, 5000);
        return result("worker_job_assignment_failed");
    }
    input_read.reset();
    output_write.reset();
    errors.reset();
    if (WaitForSingleObject(stopping.value, 0) == WAIT_OBJECT_0 ||
        !PeekNamedPipe(client, nullptr, 0, nullptr, nullptr, nullptr)) {
        TerminateJobObject(job.value, 125);
        bool stopped = WaitForSingleObject(process.value, 5000) == WAIT_OBJECT_0 &&
                       v8native::job_is_empty(job.value);
        return result("cancelled", false, false, false, 0, false, {}, {}, false, stopped);
    }
    if (ResumeThread(thread.value) == static_cast<DWORD>(-1))
        return result("worker_start_failed");
    // A bounded request cannot block the service while an authentication API is
    // stuck. The writer is joined after worker/job termination closes its reader.
    std::thread writer([&] {
        DWORD written = 0;
        WriteFile(input_write.value, json.data(), static_cast<DWORD>(json.size()), &written,
                  nullptr);
        input_write.reset();
    });
    struct WriterJoin {
        std::thread &writer;
        HANDLE job;
        HANDLE process;
        ~WriterJoin() {
            if (writer.joinable()) {
                TerminateJobObject(job, 125);
                WaitForSingleObject(process, 5000);
                CancelSynchronousIo(writer.native_handle());
                writer.join();
            }
        }
    } join_writer{writer, job.value, process.value};
    std::string reply;
    reply.reserve(kMaxReply);
    ULONGLONG started = GetTickCount64(), authenticated = 0;
    std::string failure;
    while (true) {
        DWORD available = 0;
        for (unsigned chunk = 0;
             chunk < 16 &&
             PeekNamedPipe(output_read.value, nullptr, 0, nullptr, &available, nullptr) &&
             available;
             ++chunk) {
            char bytes_out[4096];
            DWORD read = 0;
            if (!ReadFile(output_read.value, bytes_out,
                          std::min<DWORD>(available, sizeof(bytes_out)), &read, nullptr) ||
                !read)
                break;
            if (reply.size() + read > kMaxReply) {
                failure = "worker_output_invalid";
                break;
            }
            reply.append(bytes_out, read);
        }
        if (!authenticated) {
            size_t prefix = reply.rfind("V8_AUTHENTICATED\r\n", 0) == 0 ? 18
                            : reply.rfind("V8_AUTHENTICATED\n", 0) == 0 ? 17
                                                                        : 0;
            if (prefix) {
                reply.erase(0, prefix);
                authenticated = GetTickCount64();
            }
        }
        if (!failure.empty())
            break;
        if (WaitForSingleObject(process.value, 0) == WAIT_OBJECT_0)
            break;
        if (WaitForSingleObject(stopping.value, 0) == WAIT_OBJECT_0 ||
            !PeekNamedPipe(client, nullptr, 0, nullptr, nullptr, nullptr)) {
            failure = "cancelled";
            break;
        }
        ULONGLONG now = GetTickCount64();
        if (!authenticated && now - started >= 15000) {
            failure = "authentication_timed_out";
            break;
        }
        if (authenticated && now - authenticated >= static_cast<ULONGLONG>(timeout + 5) * 1000) {
            failure = "timed_out";
            break;
        }
        Sleep(20);
    }
    TerminateJobObject(job.value, 125);
    bool stopped = WaitForSingleObject(process.value, 5000) == WAIT_OBJECT_0 &&
                   v8native::job_is_empty(job.value);
    CancelSynchronousIo(writer.native_handle());
    writer.join();
    if (!json.empty())
        SecureZeroMemory(json.data(), json.size());
    if (!failure.empty())
        return result(failure, false, false, false, 0, false, {}, {}, false, stopped, false);
    if (!stopped)
        return result("outcome_unknown", false, false, false, 0, false, {}, {}, false, false,
                      false);
    DWORD available = 0;
    while (PeekNamedPipe(output_read.value, nullptr, 0, nullptr, &available, nullptr) &&
           available && reply.size() < kMaxReply) {
        char bytes_out[4096];
        DWORD read = 0;
        if (!ReadFile(output_read.value, bytes_out, std::min<DWORD>(available, sizeof(bytes_out)),
                      &read, nullptr) ||
            !read)
            break;
        reply.append(bytes_out, read);
    }
    if (reply.empty() || reply.size() > kMaxReply || reply.front() != '{')
        return result("worker_result_unverified", false, false, false, 0, false, {}, {}, false,
                      stopped, false);
    return reply;
}
static void WINAPI service_main(DWORD, wchar_t **) {
    service_handle = RegisterServiceCtrlHandlerExW(kServiceName, control, nullptr);
    if (!service_handle)
        return;
    report(SERVICE_START_PENDING);
    try {
        if (v8native::process_sid(GetCurrentProcessId()) != L"S-1-5-18") {
            report(SERVICE_STOPPED, ERROR_ACCESS_DENIED);
            return;
        }
        auto sid = configured_client_sid();
        if (sid.empty()) {
            report(SERVICE_STOPPED, ERROR_INVALID_DATA);
            return;
        }
        stopping.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!stopping.valid()) {
            report(SERVICE_STOPPED, ERROR_NOT_ENOUGH_MEMORY);
            return;
        }
        std::wstring acl = L"O:SYG:SYD:P(D;;GA;;;NU)(A;;GA;;;SY)(A;;GRGW;;;" + sid + L")";
        PSECURITY_DESCRIPTOR descriptor = nullptr;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl.c_str(), SDDL_REVISION_1,
                                                                  &descriptor, nullptr)) {
            report(SERVICE_STOPPED, ERROR_INVALID_SECURITY_DESCR);
            return;
        }
        struct Descriptor {
            PSECURITY_DESCRIPTOR p;
            ~Descriptor() {
                LocalFree(p);
            }
        } descriptor_owner{descriptor};
        SECURITY_ATTRIBUTES security{sizeof(security), descriptor, FALSE};
        report(SERVICE_RUNNING);
        while (WaitForSingleObject(stopping.value, 0) != WAIT_OBJECT_0) {
            Handle pipe(CreateNamedPipeW(
                kPipeName,
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1, static_cast<DWORD>(kMaxReply), static_cast<DWORD>(kMaxRequest), 1000,
                &security));
            if (!pipe.valid() || !v8native::connect_pipe(pipe.value, stopping.value))
                break;
            std::string json;
            struct Wipe {
                std::string &s;
                ~Wipe() {
                    if (!s.empty())
                        SecureZeroMemory(s.data(), s.size());
                }
            } wipe{json};
            if (!receive_frame(pipe.value, json, kMaxRequest, now_ms() + 3000, stopping.value))
                continue;
            std::string failure = sid == configured_client_sid() && verify_client(pipe.value, sid)
                                      ? ""
                                      : "caller_not_allowed";
            Request request;
            if (failure.empty())
                failure = parse_request(json, request);
            if (failure.empty())
                failure = validate_request(request, now_ms());
            if (failure.empty())
                failure = claim_request(request);
            DWORD timeout = request.timeout_seconds;
            request.clear_password();
            std::string response =
                failure.empty() ? supervise_worker(pipe.value, json, timeout) : result(failure);
            if (!json.empty())
                SecureZeroMemory(json.data(), json.size());
            if (send_frame(pipe.value, response, now_ms() + 2000, stopping.value)) {
                // DisconnectNamedPipe discards unread output. Wait for the
                // client's bounded receipt ACK, not an unbounded flush.
                BYTE acknowledged = 0;
                v8native::pipe_io(pipe.value, &acknowledged, 1, false, now_ms() + 2000, stopping.value);
            }
            DisconnectNamedPipe(pipe.value);
        }
        report(SERVICE_STOPPED);
    } catch (...) {
        report(SERVICE_STOPPED, ERROR_GEN_FAILURE);
    }
}
int service_dispatch() {
    SERVICE_TABLE_ENTRYW entries[]{{const_cast<LPWSTR>(kServiceName), service_main},
                                   {nullptr, nullptr}};
    return StartServiceCtrlDispatcherW(entries) ? 0 : 2;
}
} // namespace v8system
