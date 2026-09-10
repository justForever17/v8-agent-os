#include "windows.h"
#include <algorithm>
#include <sddl.h>
#include <vector>
namespace v8native {
bool job_is_empty(HANDLE job, DWORD wait_ms) {
    const ULONGLONG deadline = GetTickCount64() + wait_ms;
    do {
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION state{};
        if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &state,
                                       sizeof(state), nullptr))
            return false;
        if (state.ActiveProcesses == 0)
            return true;
        if (GetTickCount64() >= deadline)
            return false;
        Sleep(20);
    } while (true);
}
uint64_t now_ms() {
    FILETIME ft{};
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER value{};
    value.LowPart = ft.dwLowDateTime;
    value.HighPart = ft.dwHighDateTime;
    return value.QuadPart / 10000 - 11644473600000ULL;
}

std::wstring token_sid(HANDLE token) {
    DWORD size = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &size);
    if (!size || size > 65536)
        return {};
    std::vector<BYTE> info(size);
    if (!GetTokenInformation(token, TokenUser, info.data(), size, &size))
        return {};
    wchar_t *sid = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER *>(info.data())->User.Sid, &sid))
        return {};
    std::wstring result(sid);
    LocalFree(sid);
    return result;
}
std::wstring process_sid(DWORD pid) {
    Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    HANDLE raw = nullptr;
    if (!process.valid() || !OpenProcessToken(process.value, TOKEN_QUERY, &raw))
        return {};
    Handle token(raw);
    return token_sid(token.value);
}

bool pipe_io(HANDLE pipe, void *buffer, DWORD bytes, bool writing, uint64_t deadline, HANDLE stop,
             DWORD max_wait_ms) {
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event.valid())
        return false;
    OVERLAPPED operation{};
    operation.hEvent = event.value;
    DWORD transferred = 0;
    BOOL immediate = writing ? WriteFile(pipe, buffer, bytes, &transferred, &operation)
                             : ReadFile(pipe, buffer, bytes, &transferred, &operation);
    if (!immediate && GetLastError() != ERROR_IO_PENDING)
        return false;
    if (!immediate) {
        HANDLE waits[]{event.value, stop};
        uint64_t now = now_ms();
        DWORD wait = WaitForMultipleObjects(stop ? 2 : 1, waits, FALSE,
                                            static_cast<DWORD>(std::min<uint64_t>(
                                                deadline > now ? deadline - now : 0, max_wait_ms)));
        if (wait != WAIT_OBJECT_0) {
            CancelIoEx(pipe, &operation);
            GetOverlappedResult(pipe, &operation, &transferred, TRUE);
            return false;
        }
    }
    return GetOverlappedResult(pipe, &operation, &transferred, FALSE) && transferred == bytes;
}
bool connect_pipe(HANDLE pipe, HANDLE stop) {
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event.valid())
        return false;
    OVERLAPPED operation{};
    operation.hEvent = event.value;
    if (ConnectNamedPipe(pipe, &operation))
        return true;
    DWORD error = GetLastError();
    if (error == ERROR_PIPE_CONNECTED)
        return true;
    if (error != ERROR_IO_PENDING)
        return false;
    HANDLE waits[]{event.value, stop};
    DWORD wait = WaitForMultipleObjects(2, waits, FALSE, INFINITE);
    DWORD transferred = 0;
    if (wait != WAIT_OBJECT_0) {
        CancelIoEx(pipe, &operation);
        GetOverlappedResult(pipe, &operation, &transferred, TRUE);
        return false;
    }
    return GetOverlappedResult(pipe, &operation, &transferred, FALSE) != FALSE;
}
} // namespace v8native
