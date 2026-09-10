#pragma once
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <cstdint>
#include <string>
#include <windows.h>
namespace v8native {
class Handle {
  public:
    HANDLE value = nullptr;
    explicit Handle(HANDLE h = nullptr) : value(h) {}
    ~Handle() {
        reset();
    }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
    void reset(HANDLE h = nullptr) {
        if (value && value != INVALID_HANDLE_VALUE)
            CloseHandle(value);
        value = h;
    }
    bool valid() const {
        return value && value != INVALID_HANDLE_VALUE;
    }
};
uint64_t now_ms();
std::wstring token_sid(HANDLE token);
std::wstring process_sid(DWORD pid);
bool pipe_io(HANDLE, void *, DWORD, bool, uint64_t, HANDLE stop = nullptr,
             DWORD max_wait_ms = 60000);
bool connect_pipe(HANDLE, HANDLE stop);
bool job_is_empty(HANDLE job, DWORD wait_ms = 5000);
} // namespace v8native
