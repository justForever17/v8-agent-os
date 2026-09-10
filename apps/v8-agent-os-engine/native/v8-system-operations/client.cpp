#include "protocol.h"
#include <cstdio>
#include <fcntl.h>
#include <io.h>

using namespace v8system;
static int client(int argc, wchar_t **argv) {
    if (argc != 2) {
        std::puts(result("invalid_request").c_str());
        return 2;
    }
    if (!wcscmp(argv[1], L"--service"))
        return service_dispatch();
    if (!wcscmp(argv[1], L"--worker"))
        return worker_entry();
    if (wcscmp(argv[1], L"--status") && wcscmp(argv[1], L"--execute")) {
        std::puts(result("invalid_request").c_str());
        return 2;
    }
    auto info = service_info();
    auto allowed = configured_client_sid();
    bool caller_allowed =
        !allowed.empty() && v8native::process_sid(GetCurrentProcessId()) == allowed;
    if (!wcscmp(argv[1], L"--status")) {
        bool available = false;
        if (info.registered && info.running && caller_allowed) {
            Handle pipe(CreateFileW(
                kPipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr));
            available = pipe.valid() && verify_server(pipe.value, info);
        }
        std::printf("{\"ok\":true,\"code\":\"status\",\"registered\":%s,\"serviceRunning\":%s,"
                    "\"callerAllowed\":%s,\"available\":%s,\"elevated\":false,\"verified\":false,"
                    "\"protocolVersion\":1}\n",
                    info.registered ? "true" : "false", info.running ? "true" : "false",
                    caller_allowed ? "true" : "false", available ? "true" : "false");
        return 0;
    }
    DWORD mode = 0;
    if (GetConsoleMode(GetStdHandle(STD_INPUT_HANDLE), &mode)) {
        std::puts(result("private_stdin_required").c_str());
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
    if (failure.empty())
        failure = validate_request(request, now_ms());
    if (failure.empty() && !info.registered)
        failure = "privilege_component_required";
    if (failure.empty() && !caller_allowed)
        failure = "caller_not_allowed";
    if (failure.empty() && !info.running)
        failure = "service_not_running";
    if (!failure.empty()) {
        std::puts(result(failure).c_str());
        return 2;
    }
    WaitNamedPipeW(kPipeName, 1000);
    Handle pipe(CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                            FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION,
                            nullptr));
    if (!pipe.valid() || !verify_server(pipe.value, info)) {
        std::puts(result("service_peer_untrusted").c_str());
        return 2;
    }
    mode = PIPE_READMODE_MESSAGE;
    if (!SetNamedPipeHandleState(pipe.value, &mode, nullptr, nullptr)) {
        std::puts(result("pipe_unavailable").c_str());
        return 2;
    }
    DWORD wait = (request.timeout_seconds + 20) * 1000;
    uint64_t deadline = now_ms() + wait;
    if (!send_frame(pipe.value, json, request.expires_at)) {
        std::puts(
            result("outcome_unknown", false, false, false, 0, false, {}, {}, false, false, false)
                .c_str());
        return 2;
    }
    request.clear_password();
    if (!json.empty())
        SecureZeroMemory(json.data(), json.size());
    std::string response;
    if (!receive_frame(pipe.value, response, kMaxReply, deadline, nullptr, wait)) {
        std::puts(
            result("outcome_unknown", false, false, false, 0, false, {}, {}, false, false, false)
                .c_str());
        return 2;
    }
    BYTE acknowledged = 6;
    v8native::pipe_io(pipe.value, &acknowledged, 1, true, now_ms() + 2000);
    // Response comes only from the SCM-pinned SYSTEM peer. Engine validates the
    // result contract and actual verified/elevated/exit fields, not helper exit 0.
    std::puts(response.c_str());
    return 0;
}
int wmain(int argc, wchar_t **argv) {
    try {
        return client(argc, argv);
    } catch (...) {
        std::puts(result("native_component_failed").c_str());
        return 2;
    }
}
