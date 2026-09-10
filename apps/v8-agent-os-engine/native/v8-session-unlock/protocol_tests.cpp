#include "protocol.h"
#include <cstdio>
#include <cstdlib>
#include <thread>

using namespace v8unlock;
static unsigned checks = 0;
static void check(bool condition) { ++checks; if (!condition) { std::fprintf(stderr, "protocol assertion %u failed\n", checks); std::exit(1); } }
static std::string fixture(const std::string& password = "fixture-only") {
    return "{\"username\":\"fixture-user\",\"domain\":\"fixture-domain\",\"password\":\"" + password + "\",\"requestId\":\"24363516-7eaa-4a3d-90a4-093b47abebf4\",\"expiresAt\":150000,\"sessionId\":1}";
}
static void pipe_response_checks() {
    // Use only owned, non-authenticating pipes. No installed Provider is contacted.
    for (unsigned mode = 0; mode < 5; ++mode) {
        const auto name = L"\\\\.\\pipe\\V8UnlockProtocolTest." +
            std::to_wstring(GetCurrentProcessId()) + L"." + std::to_wstring(mode);
        Handle server(CreateNamedPipeW(name.c_str(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
            1, 4096, 4096, 1000, nullptr));
        check(server.valid());
        Handle peer(CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
            OPEN_EXISTING, FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION, nullptr));
        check(peer.valid());
        DWORD read_mode = PIPE_READMODE_MESSAGE;
        check(SetNamedPipeHandleState(peer.value, &read_mode, nullptr, nullptr) != FALSE);
        if (mode == 0) {
            ULONG pid = 0;
            check(GetNamedPipeServerProcessId(peer.value, &pid) && pid == GetCurrentProcessId());
            check(!verify_pipe_server(peer.value, current_session_id()));
            BYTE marker = 1, observed = 0;
            check(pipe_io(peer.value, &marker, 1, true, now_ms() + 1000));
            check(pipe_io(server.value, &observed, 1, false, now_ms() + 1000) && observed == marker);
            Session own;
            own.id = current_session_id(); own.sid = process_sid(GetCurrentProcessId());
            check(!own.sid.empty() && verify_pipe_client(server.value, own));
            ++own.id;
            check(!verify_pipe_client(server.value, own));
            --own.id; own.sid = L"S-1-5-21-1";
            check(!verify_pipe_client(server.value, own));
        }
        Reply response; response.code = Code::invalid_request;
        if (mode == 0) {
            // Old behavior: write completion is not client receipt. The queued
            // response is deterministically lost when disconnected before reading.
            check(pipe_io(server.value, &response, sizeof(response), true, now_ms() + 1000));
            check(DisconnectNamedPipe(server.value) != FALSE);
            Reply received;
            check(!pipe_io(peer.value, &received, sizeof(received), false, now_ms() + 1000));
            continue;
        }
        Handle sent(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        Handle stop(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        check(sent.valid() && stop.valid());
        bool written = false, receipt = false;
        ULONGLONG elapsed = 0;
        std::thread serving([&] {
            written = pipe_io(server.value, &response, sizeof(response), true, now_ms() + 1000);
            SetEvent(sent.value);
            const auto start = GetTickCount64();
            BYTE ack = 0;
            receipt = written && pipe_io(server.value, &ack, 1, false, now_ms() + 750, stop.value) && ack == 6;
            elapsed = GetTickCount64() - start;
            DisconnectNamedPipe(server.value);
        });
        check(WaitForSingleObject(sent.value, 2000) == WAIT_OBJECT_0);
        if (mode == 1) {
            Reply received;
            check(pipe_io(peer.value, &received, sizeof(received), false, now_ms() + 1000));
            check(received.magic == response.magic && received.version == response.version &&
                  received.code == response.code && received.submitted == response.submitted);
            BYTE ack = 6;
            check(pipe_io(peer.value, &ack, 1, true, now_ms() + 1000));
        } else if (mode == 2) peer.reset(); // client disappears before consuming reply
        else if (mode == 4) SetEvent(stop.value); // Provider shutdown cancels a pending receipt
        // mode 3 deliberately keeps an idle client open without acknowledging.
        serving.join();
        check(written && receipt == (mode == 1));
        check(elapsed < 2000);
    }
}
int main() {
    SecretRequest input;
    check(parse_request(fixture(), input.value) == Code::accepted);
    check(validate_request(input.value, 100000) == Code::accepted);
    check(validate_request(input.value, 150000) == Code::expired_request);
    check(validate_request(input.value, 89999) == Code::expired_request);
    check(parse_request(fixture("\\u4f60\\u597d\\ud83d\\ude00"), input.value) == Code::accepted);
    check(wcslen(input.value.password) == 4);
    check(parse_request(fixture("\\ud800"), input.value) == Code::invalid_request);
    check(parse_request(fixture("\\u0000"), input.value) == Code::invalid_request);
    check(parse_request(fixture("\\q"), input.value) == Code::invalid_request);
    check(parse_request(fixture(std::string(1025, 'x')), input.value) == Code::invalid_request);
    check(parse_request(fixture().substr(0, fixture().size() - 1), input.value) == Code::invalid_request);
    check(parse_request(fixture() + " {}", input.value) == Code::invalid_request);
    auto duplicate = fixture(); duplicate.replace(duplicate.find("domain"), 6, "username");
    check(parse_request(duplicate, input.value) == Code::invalid_request);
    auto unknown = fixture(); unknown.replace(unknown.find("domain"), 6, "script");
    check(parse_request(unknown, input.value) == Code::invalid_request);
    auto floating = fixture(); floating.replace(floating.find("150000"), 6, "150000.0");
    check(parse_request(floating, input.value) == Code::invalid_request);
    check(parse_request(std::string(kMaxJsonBytes + 1, ' '), input.value) == Code::invalid_request);
    check(parse_request(fixture(), input.value) == Code::accepted);
    input.value.version = 2;
    check(validate_request(input.value, 100000) == Code::invalid_request);
    input.value.version = kProtocol; input.value.password[0] = 0;
    check(validate_request(input.value, 100000) == Code::invalid_request);
    check(parse_request(fixture(), input.value) == Code::accepted);
    input.value.request_id[8] = 'x';
    check(validate_request(input.value, 100000) == Code::invalid_request);
    Session session; session.id = 2;
    check(validate_target(input.value, session) == Code::wrong_session);
    session.id = 1;
    check(validate_target(input.value, session) == Code::state_unknown);
    session.known = true; session.logged_on = true; session.sid = L"test-sid";
    check(validate_target(input.value, session) == Code::remote_session);
    session.id = current_session_id(); session.local = true; session.username = L"fixture-user"; session.domain = L"fixture-domain";
    input.value.session_id = session.id;
    check(validate_target(input.value, session) == Code::already_unlocked);
    session.locked = true;
    check(validate_target(input.value, session) == Code::accepted);
    wcscpy_s(input.value.domain, L"\\\\untrusted-host");
    check(validate_target(input.value, session) == Code::wrong_user);
    wcscpy_s(input.value.domain, L"fixture-domain"); wcscpy_s(input.value.username, L"other-user");
    check(validate_target(input.value, session) == Code::wrong_user);
    check(!verify_pipe_server(INVALID_HANDLE_VALUE, 1));
    check(!verify_pipe_client(INVALID_HANDLE_VALUE, session));
    check(std::string(code_name(Code::authentication_failed)) == "authentication_failed");
    pipe_response_checks();
    std::printf("%u protocol checks passed; no registration, password validation, or unlock attempted.\n", checks);
    return 0;
}
