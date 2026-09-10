#include "protocol.h"
#include <io.h>
#include <fcntl.h>
#include <cstdio>

using namespace v8unlock;
static int run_client(int argc, wchar_t** argv) {
    DWORD session_id = current_session_id();
    bool installed = provider_registered();
    if (argc != 2 || (wcscmp(argv[1], L"--status") && wcscmp(argv[1], L"--unlock"))) {
        print_result(Code::invalid_request, session_id, false, installed); return 2;
    }
    if (!wcscmp(argv[1], L"--status")) {
        auto session = inspect_session(session_id);
        Code code = !session.known || !session.logged_on ? Code::state_unknown : !session.local ? Code::remote_session : session.locked ? Code::locked : Code::already_unlocked;
        if (session.known && session.local && session_id != WTSGetActiveConsoleSessionId()) code = Code::wrong_session;
        if (session.known && session.sid.empty()) code = Code::state_unknown;
        else if (session.known && process_sid(GetCurrentProcessId()) != session.sid) code = Code::wrong_user;
        bool ready = false;
        if (code == Code::locked && installed) {
            Handle probe(CreateFileW(pipe_name(session_id).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                                    FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr));
            ready = probe.valid() && verify_pipe_server(probe.value, session_id);
        }
        print_result(code, session_id, false, installed, ready, &session);
        return session.known ? 0 : 2;
    }
    // No secret can be supplied in argv/env/file arguments or an echoing console.
    DWORD console_mode = 0;
    if (GetConsoleMode(GetStdHandle(STD_INPUT_HANDLE), &console_mode)) { print_result(Code::invalid_request, session_id, false, installed); return 2; }
    _setmode(_fileno(stdin), _O_BINARY);
    std::string json;
    json.reserve(kMaxJsonBytes + 1);
    struct Wipe { std::string& s; ~Wipe(){if(!s.empty())SecureZeroMemory(s.data(),s.size());} } wipe{json};
    for (size_t i = 0; i <= kMaxJsonBytes; ++i) {
        int byte = std::getchar();
        if (byte == EOF) break;
        json.push_back(static_cast<char>(byte));
    }
    SecretRequest request;
    Code code = parse_request(json, request.value);
    if (code == Code::accepted) code = validate_request(request.value, now_ms());
    if (code == Code::accepted && request.value.session_id != session_id) code = Code::wrong_session;
    auto initial = inspect_session(session_id);
    if (code == Code::accepted) code = validate_target(request.value, initial);
    if ((code == Code::accepted || code == Code::already_unlocked) && process_sid(GetCurrentProcessId()) != initial.sid) code = Code::wrong_user;
    if (code != Code::accepted) { print_result(code, session_id, false, installed, false, &initial); return code == Code::already_unlocked ? 0 : 2; }
    if (!installed) { print_result(Code::provider_unavailable, session_id, false, installed); return 2; }
    WaitNamedPipeW(pipe_name(session_id).c_str(), 1000);
    Handle pipe(CreateFileW(pipe_name(session_id).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                          FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION, nullptr));
    if (!pipe.valid()) { print_result(GetLastError() == ERROR_PIPE_BUSY ? Code::busy : Code::provider_unavailable, session_id, false, installed); return 2; }
    if (!verify_pipe_server(pipe.value, session_id)) { print_result(Code::peer_untrusted, session_id, false, installed); return 2; }
    DWORD mode = PIPE_READMODE_MESSAGE;
    if (!SetNamedPipeHandleState(pipe.value, &mode, nullptr, nullptr) || !pipe_io(pipe.value, &request.value, sizeof(Request), true, request.value.expires_at)) {
        print_result(Code::outcome_unknown, session_id, -1, installed); return 2;
    }
    // Clear credentials as soon as transport is done; only the expiry is needed below.
    uint64_t deadline = request.value.expires_at;
    SecureZeroMemory(&request.value, sizeof(request.value));
    if (!json.empty()) SecureZeroMemory(json.data(), json.size());
    Reply reply;
    bool received = pipe_io(pipe.value, &reply, sizeof(reply), false, deadline);
    if (received) {
        BYTE acknowledged = 6;
        pipe_io(pipe.value, &acknowledged, 1, true, now_ms() + 1000);
    }
    bool valid_reply = received && reply.magic == kMagic && reply.version == kProtocol && reply.submitted <= 1;
    int submitted = valid_reply ? static_cast<int>(reply.submitted) : -1;
    code = valid_reply ? reply.code : Code::outcome_unknown;
    // LogonUI may close the pipe immediately after successful Windows authentication.
    // Independently inspect the original console session and identity in either case.
    auto final_state = inspect_session(session_id);
    while (final_state.known && final_state.locked && now_ms() < deadline &&
           (code == Code::timeout || code == Code::cancelled || code == Code::unlocked || code == Code::accepted || code == Code::outcome_unknown)) {
        Sleep(100);
        final_state = inspect_session(session_id);
    }
    if (final_state.known && final_state.local && final_state.logged_on && !final_state.locked && final_state.sid == initial.sid)
        code = Code::unlocked;
    else if (code == Code::unlocked || code == Code::accepted || code == Code::already_unlocked)
        code = Code::state_unknown;
    print_result(code, session_id, submitted, installed, false, &final_state);
    return code == Code::unlocked ? 0 : 2;
}
int wmain(int argc, wchar_t** argv) {
    try { return run_client(argc, argv); }
    catch (...) { print_result(Code::internal_error, 0, false, false); return 2; }
}
