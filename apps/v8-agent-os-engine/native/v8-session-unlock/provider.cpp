#include <initguid.h>
#include "protocol.h"
#include <credentialprovider.h>
#include <ntsecapi.h>
#include <wincred.h>
#include <sddl.h>
#include <shlwapi.h>
#include <propkey.h>
#include <identityprovider.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>

using namespace v8unlock;
// This is an independent V2 provider, never a filter or a wrapper of an OS provider.
static const CLSID CLSID_V8Unlock = {0x793345f6, 0xc96b, 0x472a, {0xa7, 0x80, 0x38, 0x78, 0x39, 0x44, 0x00, 0x68}};
static std::atomic<long> objects{0};
static HINSTANCE module = nullptr;
static constexpr UINT kCredentialsReady = WM_APP + 141;

struct State {
    std::mutex mutex;
    std::unique_ptr<SecretRequest> request;
    Session target;
    std::wstring qualified_name;
    bool identity_provider_account = false;
    bool user_bound = false;
    bool submitted = false;
    HANDLE active_pipe = nullptr; // borrowed while worker owns the connection; protected by mutex
    Code result = Code::accepted;
    Handle stop{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    Handle completed{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
};

static HRESULT negotiate_package(ULONG* package) {
    HANDLE lsa = nullptr;
    NTSTATUS result = LsaConnectUntrusted(&lsa);
    if (result < 0) return HRESULT_FROM_WIN32(LsaNtStatusToWinError(result));
    char name[] = "Negotiate";
    LSA_STRING string{static_cast<USHORT>(sizeof(name) - 1), sizeof(name), name};
    result = LsaLookupAuthenticationPackage(lsa, &string, package);
    LsaDeregisterLogonProcess(lsa);
    return result < 0 ? HRESULT_FROM_WIN32(LsaNtStatusToWinError(result)) : S_OK;
}

// Follows the documented Microsoft V2 sample's packed KERB layout. Windows, not
// this DLL, verifies the supplied password using the Negotiate authentication package.
static HRESULT serialize_password(const Request& request, const std::wstring& qualified,
                                  bool identity_account, CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario,
                                  CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* out) {
    HRESULT hr = negotiate_package(&out->ulAuthenticationPackage);
    if (FAILED(hr)) return hr;
    out->clsidCredentialProvider = CLSID_V8Unlock;
    if (identity_account) {
        DWORD flags = CRED_PACK_PROTECTED_CREDENTIALS | CRED_PACK_ID_PROVIDER_CREDENTIALS;
        CredPackAuthenticationBufferW(flags, const_cast<PWSTR>(qualified.c_str()), const_cast<PWSTR>(request.password), nullptr, &out->cbSerialization);
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER) return HRESULT_FROM_WIN32(GetLastError());
        out->rgbSerialization = static_cast<BYTE*>(CoTaskMemAlloc(out->cbSerialization));
        if (!out->rgbSerialization) return E_OUTOFMEMORY;
        if (CredPackAuthenticationBufferW(flags, const_cast<PWSTR>(qualified.c_str()), const_cast<PWSTR>(request.password), out->rgbSerialization, &out->cbSerialization)) return S_OK;
        hr = HRESULT_FROM_WIN32(GetLastError());
    } else {
        DWORD size = 0;
        CRED_PROTECTION_TYPE protection = CredUnprotected;
        CredProtectW(FALSE, const_cast<PWSTR>(request.password), static_cast<DWORD>(wcslen(request.password) + 1), nullptr, &size, &protection);
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || !size || size > 8192) return E_FAIL;
        std::vector<wchar_t> protected_password(size);
        struct Wipe { std::vector<wchar_t>& s; ~Wipe(){SecureZeroMemory(s.data(), s.size()*sizeof(wchar_t));} } wipe{protected_password};
        if (!CredProtectW(FALSE, const_cast<PWSTR>(request.password), static_cast<DWORD>(wcslen(request.password) + 1), protected_password.data(), &size, &protection)) return HRESULT_FROM_WIN32(GetLastError());
        auto separator = qualified.find(L'\\');
        if (separator == std::wstring::npos || separator == 0 || separator + 1 == qualified.size()) return E_INVALIDARG;
        const std::wstring domain = qualified.substr(0, separator);
        const std::wstring username = qualified.substr(separator + 1);
        const wchar_t* strings[]{domain.c_str(), username.c_str(), protected_password.data()};
        DWORD lengths[3]{};
        DWORD total = sizeof(KERB_INTERACTIVE_UNLOCK_LOGON);
        for (unsigned i = 0; i < 3; ++i) { lengths[i] = static_cast<DWORD>(wcslen(strings[i]) * sizeof(wchar_t)); total += lengths[i]; }
        auto* packed = static_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(CoTaskMemAlloc(total));
        if (!packed) return E_OUTOFMEMORY;
        ZeroMemory(packed, total);
        packed->Logon.MessageType = scenario == CPUS_UNLOCK_WORKSTATION ? KerbWorkstationUnlockLogon : KerbInteractiveLogon;
        UNICODE_STRING* fields[]{&packed->Logon.LogonDomainName, &packed->Logon.UserName, &packed->Logon.Password};
        DWORD offset = sizeof(*packed);
        for (unsigned i = 0; i < 3; ++i) {
            fields[i]->Length = fields[i]->MaximumLength = static_cast<USHORT>(lengths[i]);
            fields[i]->Buffer = reinterpret_cast<PWSTR>(static_cast<ULONG_PTR>(offset));
            memcpy(reinterpret_cast<BYTE*>(packed) + offset, strings[i], lengths[i]);
            offset += lengths[i];
        }
        out->rgbSerialization = reinterpret_cast<BYTE*>(packed);
        out->cbSerialization = total;
        return S_OK;
    }
    if (out->rgbSerialization) { SecureZeroMemory(out->rgbSerialization, out->cbSerialization); CoTaskMemFree(out->rgbSerialization); }
    ZeroMemory(out, sizeof(*out));
    return hr;
}

class Credential final : public ICredentialProviderCredential2 {
    std::atomic<ULONG> refs_{1};
    std::shared_ptr<State> state_;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario_;
public:
    Credential(std::shared_ptr<State> state, CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario) : state_(std::move(state)), scenario_(scenario) { ++objects; }
    ~Credential() { --objects; }
    IFACEMETHODIMP QueryInterface(REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (id == IID_IUnknown || id == IID_ICredentialProviderCredential || id == IID_ICredentialProviderCredential2) *value = static_cast<ICredentialProviderCredential2*>(this);
        if (!*value) return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return ++refs_; }
    IFACEMETHODIMP_(ULONG) Release() override { ULONG count = --refs_; if (!count) delete this; return count; }
    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents*) override { return S_OK; }
    IFACEMETHODIMP UnAdvise() override { return S_OK; }
    IFACEMETHODIMP SetSelected(BOOL* automatic) override { if (!automatic) return E_POINTER; *automatic = FALSE; return S_OK; }
    IFACEMETHODIMP SetDeselected() override {
        std::lock_guard<std::mutex> lock(state_->mutex);
        if (state_->request && !state_->submitted) {
            state_->request.reset(); state_->result = Code::cancelled; SetEvent(state_->completed.value);
        }
        return S_OK;
    }
    IFACEMETHODIMP GetFieldState(DWORD field, CREDENTIAL_PROVIDER_FIELD_STATE* state, CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* interactive) override {
        if (field || !state || !interactive) return E_INVALIDARG;
        *state = CPFS_DISPLAY_IN_BOTH; *interactive = CPFIS_NONE; return S_OK;
    }
    IFACEMETHODIMP GetStringValue(DWORD field, PWSTR* value) override { return field == 0 && value ? SHStrDupW(L"V8OS authorized session unlock", value) : E_INVALIDARG; }
    IFACEMETHODIMP GetBitmapValue(DWORD, HBITMAP* value) override { if (value) *value = nullptr; return E_NOTIMPL; }
    IFACEMETHODIMP GetCheckboxValue(DWORD, BOOL*, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetSubmitButtonValue(DWORD, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueCount(DWORD, DWORD*, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueAt(DWORD, DWORD, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetStringValue(DWORD, PCWSTR) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetCheckboxValue(DWORD, BOOL) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD, DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP CommandLinkClicked(DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetUserSid(PWSTR* sid) override {
        if (!sid) return E_POINTER;
        std::lock_guard<std::mutex> lock(state_->mutex);
        return state_->user_bound ? SHStrDupW(state_->target.sid.c_str(), sid) : E_UNEXPECTED;
    }
    IFACEMETHODIMP GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* response,
                                    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization,
                                    PWSTR* status_text, CREDENTIAL_PROVIDER_STATUS_ICON* icon) override try {
        if (!response || !serialization || !status_text || !icon) return E_POINTER;
        *response = CPGSR_NO_CREDENTIAL_NOT_FINISHED; *status_text = nullptr; *icon = CPSI_NONE;
        ZeroMemory(serialization, sizeof(*serialization));
        std::lock_guard<std::mutex> lock(state_->mutex);
        if (!state_->request || state_->submitted || WaitForSingleObject(state_->stop.value, 0) == WAIT_OBJECT_0) return S_OK;
        auto request = std::move(state_->request); // one serialization attempt, including failures
        Code valid = validate_request(request->value, now_ms());
        if (valid == Code::accepted && (!state_->active_pipe || !PeekNamedPipe(state_->active_pipe, nullptr, 0, nullptr, nullptr, nullptr))) valid = Code::cancelled;
        if (valid == Code::accepted) valid = validate_target(request->value, inspect_session(state_->target.id));
        if (valid != Code::accepted) { state_->result = valid; SetEvent(state_->completed.value); return S_OK; }
        HRESULT hr = serialize_password(request->value, state_->qualified_name, state_->identity_provider_account, scenario_, serialization);
        if (FAILED(hr)) { state_->result = Code::internal_error; SetEvent(state_->completed.value); return hr; }
        // Last local cancellation fence. Once RETURN_CREDENTIAL_FINISHED reaches
        // LogonUI/LSA there is no supported retract; callers must inspect OS state.
        if (!PeekNamedPipe(state_->active_pipe, nullptr, 0, nullptr, nullptr, nullptr) || now_ms() >= request->value.expires_at) {
            SecureZeroMemory(serialization->rgbSerialization, serialization->cbSerialization);
            CoTaskMemFree(serialization->rgbSerialization); ZeroMemory(serialization, sizeof(*serialization));
            state_->result = Code::cancelled; SetEvent(state_->completed.value); return S_OK;
        }
        state_->submitted = true;
        *response = CPGSR_RETURN_CREDENTIAL_FINISHED;
        return S_OK;
    } catch (...) {
        std::lock_guard<std::mutex> lock(state_->mutex);
        state_->result = Code::internal_error; SetEvent(state_->completed.value);
        return E_OUTOFMEMORY;
    }
    IFACEMETHODIMP ReportResult(NTSTATUS status, NTSTATUS substatus, PWSTR* text, CREDENTIAL_PROVIDER_STATUS_ICON* icon) override {
        if (!text || !icon) return E_POINTER;
        *text = nullptr; *icon = CPSI_NONE;
        if (status >= 0) return S_OK; // only the client-side WTS observation can declare unlocked
        std::lock_guard<std::mutex> lock(state_->mutex);
        const auto detail = static_cast<ULONG>(substatus ? substatus : status);
        state_->result = detail == 0xc0000071 || detail == 0xc0000224 ? Code::password_expired :
                         detail == 0xc000006e || detail == 0xc0000072 || detail == 0xc0000234 ? Code::account_restricted : Code::authentication_failed;
        state_->request.reset();
        SetEvent(state_->completed.value);
        *icon = CPSI_ERROR;
        return SHStrDupW(L"V8OS unlock was not authenticated. Use a Windows sign-in option or update the configured credential.", text);
    }
};

class Provider final : public ICredentialProvider, public ICredentialProviderSetUserArray {
    std::atomic<ULONG> refs_{1};
    std::shared_ptr<State> state_{std::make_shared<State>()};
    ICredentialProviderEvents* events_ = nullptr;
    UINT_PTR advise_context_ = 0;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario_ = CPUS_INVALID;
    HWND window_ = nullptr;
    std::thread worker_;

    static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM first, LPARAM second) {
        auto* self = reinterpret_cast<Provider*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (message == WM_NCCREATE) {
            self = static_cast<Provider*>(reinterpret_cast<CREATESTRUCTW*>(second)->lpCreateParams);
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (message == kCredentialsReady && self && self->events_) {
            self->events_->CredentialsChanged(self->advise_context_);
            return 0;
        }
        return DefWindowProcW(window, message, first, second);
    }
    void stop() {
        SetEvent(state_->stop.value);
        if (worker_.joinable()) worker_.join();
        { std::lock_guard<std::mutex> lock(state_->mutex); state_->request.reset(); }
        if (window_) { DestroyWindow(window_); window_ = nullptr; }
        UnregisterClassW(L"V8SessionUnlockEventWindow", module);
        if (events_) { events_->Release(); events_ = nullptr; }
    }
    void start() noexcept try {
        if (worker_.joinable() || !events_ || !window_ || !state_->user_bound || !state_->stop.valid() || !state_->completed.valid()) return;
        ResetEvent(state_->stop.value);
        auto state = state_;
        HWND window = window_;
        worker_ = std::thread([state, window] {
          try {
            const std::wstring acl = L"O:SYG:SYD:P(D;;GA;;;NU)(A;;GA;;;SY)(A;;GRGW;;;" + state->target.sid + L")";
            PSECURITY_DESCRIPTOR descriptor = nullptr;
            if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) return;
            struct FreeDescriptor { PSECURITY_DESCRIPTOR p; ~FreeDescriptor(){LocalFree(p);} } free_descriptor{descriptor};
            SECURITY_ATTRIBUTES security{sizeof(security), descriptor, FALSE};
            while (WaitForSingleObject(state->stop.value, 0) != WAIT_OBJECT_0) {
                Handle pipe(CreateNamedPipeW(pipe_name(state->target.id).c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, sizeof(Reply), sizeof(Request), 1000, &security));
                if (!pipe.valid() || !connect_pipe(pipe.value, state->stop.value)) break;
                SecretRequest input;
                if (!pipe_io(pipe.value, &input.value, sizeof(Request), false, now_ms() + 3000, state->stop.value)) continue;
                Reply reply;
                Session current = inspect_session(state->target.id);
                reply.code = verify_pipe_client(pipe.value, current) ? validate_request(input.value, now_ms()) : Code::peer_untrusted;
                if (reply.code == Code::accepted) reply.code = validate_target(input.value, current);
                if (reply.code == Code::accepted && current.sid != state->target.sid) reply.code = Code::wrong_user;
                if (reply.code == Code::accepted) reply.code = claim_request(input.value);
                if (reply.code == Code::accepted) {
                    ResetEvent(state->completed.value);
                    {
                        std::lock_guard<std::mutex> lock(state->mutex);
                        state->submitted = false; state->result = Code::accepted;
                        state->active_pipe = pipe.value;
                        state->request = std::make_unique<SecretRequest>();
                        memcpy(&state->request->value, &input.value, sizeof(Request));
                    }
                    SecureZeroMemory(input.value.password, sizeof(input.value.password));
                    if (!PostMessageW(window, kCredentialsReady, 0, 0)) reply.code = Code::internal_error;
                    while (reply.code == Code::accepted && now_ms() < input.value.expires_at) {
                        HANDLE waits[]{state->stop.value, state->completed.value};
                        DWORD wait = WaitForMultipleObjects(2, waits, FALSE, 100);
                        if (wait == WAIT_OBJECT_0) { reply.code = Code::cancelled; break; }
                        if (wait == WAIT_OBJECT_0 + 1) { std::lock_guard<std::mutex> lock(state->mutex); reply.code = state->result; break; }
                        if (!PeekNamedPipe(pipe.value, nullptr, 0, nullptr, nullptr, nullptr)) {
                            std::lock_guard<std::mutex> lock(state->mutex);
                            reply.code = state->submitted ? Code::outcome_unknown : Code::cancelled;
                            state->request.reset(); break;
                        }
                        auto observed = inspect_session(state->target.id);
                        if (observed.known && observed.logged_on && observed.local && !observed.locked && observed.sid == state->target.sid) { reply.code = Code::unlocked; break; }
                        if (!observed.known || observed.sid != state->target.sid) { reply.code = Code::state_unknown; break; }
                    }
                    if (reply.code == Code::accepted) reply.code = Code::timeout;
                    {
                        std::lock_guard<std::mutex> lock(state->mutex);
                        reply.submitted = state->submitted ? 1 : 0;
                        if (state->submitted && (reply.code == Code::timeout || reply.code == Code::cancelled)) reply.code = Code::outcome_unknown;
                        state->request.reset();
                        state->active_pipe = nullptr;
                    }
                }
                SecureZeroMemory(input.value.password, sizeof(input.value.password));
                if (pipe_io(pipe.value, &reply, sizeof(reply), true, now_ms() + 1000, state->stop.value)) {
                    BYTE acknowledged = 0;
                    pipe_io(pipe.value, &acknowledged, 1, false, now_ms() + 1000, state->stop.value);
                }
                DisconnectNamedPipe(pipe.value);
            }
          } catch (...) {
            std::lock_guard<std::mutex> lock(state->mutex);
            state->request.reset(); state->result = Code::internal_error; SetEvent(state->completed.value);
          }
        });
    } catch (...) {
        std::lock_guard<std::mutex> lock(state_->mutex);
        state_->result = Code::internal_error; SetEvent(state_->completed.value);
    }
public:
    Provider() { ++objects; }
    ~Provider() { stop(); --objects; }
    IFACEMETHODIMP QueryInterface(REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (id == IID_IUnknown || id == IID_ICredentialProvider) *value = static_cast<ICredentialProvider*>(this);
        else if (id == IID_ICredentialProviderSetUserArray) *value = static_cast<ICredentialProviderSetUserArray*>(this);
        if (!*value) return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return ++refs_; }
    IFACEMETHODIMP_(ULONG) Release() override { ULONG count = --refs_; if (!count) delete this; return count; }
    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario, DWORD) override try {
        if (worker_.joinable()) stop();
        state_->user_bound = false;
        state_->qualified_name.clear();
        if (scenario != CPUS_LOGON && scenario != CPUS_UNLOCK_WORKSTATION) return E_NOTIMPL;
        state_->target = inspect_session(current_session_id());
        if (!state_->target.known || !state_->target.local || !state_->target.logged_on || !state_->target.locked || state_->target.sid.empty() || state_->target.id != WTSGetActiveConsoleSessionId()) return E_NOTIMPL;
        scenario_ = scenario;
        return S_OK;
    } catch (...) { return E_OUTOFMEMORY; }
    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*) override { return E_NOTIMPL; }
    IFACEMETHODIMP Advise(ICredentialProviderEvents* events, UINT_PTR context) override try {
        if (!events) return E_INVALIDARG;
        if (events_) stop();
        events_ = events; events_->AddRef(); advise_context_ = context;
        WNDCLASSW cls{}; cls.lpfnWndProc = window_proc; cls.hInstance = module; cls.lpszClassName = L"V8SessionUnlockEventWindow";
        RegisterClassW(&cls);
        window_ = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, module, this);
        if (!window_) return HRESULT_FROM_WIN32(GetLastError());
        start(); return S_OK;
    } catch (...) { return E_OUTOFMEMORY; }
    IFACEMETHODIMP UnAdvise() override { stop(); return S_OK; }
    IFACEMETHODIMP SetUserArray(ICredentialProviderUserArray* users) override try {
        if (!users || worker_.joinable()) return E_INVALIDARG;
        DWORD count = 0;
        if (FAILED(users->GetCount(&count))) return E_FAIL;
        for (DWORD i = 0; i < count; ++i) {
            ICredentialProviderUser* user = nullptr;
            if (FAILED(users->GetAt(i, &user))) continue;
            PWSTR sid = nullptr, qualified = nullptr;
            GUID provider{};
            user->GetSid(&sid);
            bool matches = sid && state_->target.sid == sid;
            if (matches) {
                user->GetStringValue(PKEY_Identity_QualifiedUserName, &qualified);
                user->GetProviderID(&provider);
                state_->user_bound = qualified && *qualified;
                if (state_->user_bound) {
                    state_->qualified_name = qualified;
                    state_->identity_provider_account = provider != Identity_LocalUserProvider;
                }
            }
            CoTaskMemFree(sid); CoTaskMemFree(qualified); user->Release();
            if (matches) break;
        }
        start();
        return S_OK;
    } catch (...) { return E_OUTOFMEMORY; }
    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* count) override { if (!count) return E_POINTER; *count = 1; return S_OK; }
    IFACEMETHODIMP GetFieldDescriptorAt(DWORD index, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** descriptor) override {
        if (!descriptor) return E_POINTER;
        *descriptor = nullptr;
        if (index) return E_INVALIDARG;
        auto* value = static_cast<CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*>(CoTaskMemAlloc(sizeof(CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR)));
        if (!value) return E_OUTOFMEMORY;
        ZeroMemory(value, sizeof(*value)); value->cpft = CPFT_LARGE_TEXT;
        HRESULT hr = SHStrDupW(L"V8OS authorized session unlock", &value->pszLabel);
        if (FAILED(hr)) { CoTaskMemFree(value); return hr; }
        *descriptor = value; return S_OK;
    }
    IFACEMETHODIMP GetCredentialCount(DWORD* count, DWORD* selected, BOOL* automatic) override {
        if (!count || !selected || !automatic) return E_POINTER;
        start();
        std::lock_guard<std::mutex> lock(state_->mutex);
        bool ready = state_->user_bound && state_->request && !state_->submitted && validate_request(state_->request->value, now_ms()) == Code::accepted;
        *count = ready ? 1 : 0; *selected = ready ? 0 : CREDENTIAL_PROVIDER_NO_DEFAULT; *automatic = ready ? TRUE : FALSE;
        return S_OK;
    }
    IFACEMETHODIMP GetCredentialAt(DWORD index, ICredentialProviderCredential** credential) override {
        if (!credential) return E_POINTER;
        *credential = nullptr;
        if (index || !state_->user_bound) return E_INVALIDARG;
        *credential = new(std::nothrow) Credential(state_, scenario_);
        return *credential ? S_OK : E_OUTOFMEMORY;
    }
};

class Factory final : public IClassFactory {
    std::atomic<ULONG> refs_{1};
public:
    Factory(){++objects;} ~Factory(){--objects;}
    IFACEMETHODIMP QueryInterface(REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (id != IID_IUnknown && id != IID_IClassFactory) return E_NOINTERFACE;
        *value = static_cast<IClassFactory*>(this); AddRef(); return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return ++refs_; }
    IFACEMETHODIMP_(ULONG) Release() override { ULONG count=--refs_;if(!count)delete this;return count; }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID id, void** value) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* provider = new(std::nothrow) Provider();
        if (!provider) return E_OUTOFMEMORY;
        HRESULT hr = provider->QueryInterface(id, value); provider->Release(); return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override { if(lock)++objects;else --objects;return S_OK; }
};
extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) { module = instance; DisableThreadLibraryCalls(instance); }
    return TRUE;
}
STDAPI DllCanUnloadNow() { return objects == 0 ? S_OK : S_FALSE; }
STDAPI DllGetClassObject(REFCLSID clsid, REFIID id, void** value) try {
    if (clsid != CLSID_V8Unlock) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new(std::nothrow) Factory();
    if (!factory) return E_OUTOFMEMORY;
    HRESULT hr = factory->QueryInterface(id, value); factory->Release(); return hr;
} catch (...) { return E_OUTOFMEMORY; }
