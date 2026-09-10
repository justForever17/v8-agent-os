// Same production Credential implementation; no COM registration or LogonUI use.
#include "provider.cpp"
#include <cstdio>
#include <cstdlib>
static unsigned count = 0;
static void require(bool valid) { ++count; if (!valid) { std::fprintf(stderr, "provider assertion %u failed\n", count); std::exit(1); } }
int main() {
    HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    require(SUCCEEDED(initialized));
    auto state = std::make_shared<State>();
    state->target.id = current_session_id();
    state->target.sid = L"S-1-5-21-1-2-3-4";
    state->user_bound = true;
    auto* credential = new Credential(state, CPUS_LOGON);
    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE response;
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION serialized{};
    PWSTR text = nullptr;
    CREDENTIAL_PROVIDER_STATUS_ICON icon;
    require(SUCCEEDED(credential->GetSerialization(&response, &serialized, &text, &icon)));
    require(response == CPGSR_NO_CREDENTIAL_NOT_FINISHED && serialized.rgbSerialization == nullptr);
    state->request = std::make_unique<SecretRequest>();
    state->request->value.expires_at = 1;
    require(SUCCEEDED(credential->GetSerialization(&response, &serialized, &text, &icon)));
    require(!state->request && !state->submitted && serialized.rgbSerialization == nullptr);
    require(SUCCEEDED(credential->GetSerialization(&response, &serialized, &text, &icon)));
    require(response == CPGSR_NO_CREDENTIAL_NOT_FINISHED);
    state->request = std::make_unique<SecretRequest>();
    auto& pending = state->request->value;
    pending.session_id = 1; pending.expires_at = now_ms() + 30000;
    wcscpy_s(pending.username, L"fixture"); wcscpy_s(pending.password, L"fixture-not-an-os-password");
    wcscpy_s(pending.request_id, L"24363516-7eaa-4a3d-90a4-093b47abebf4");
    require(SUCCEEDED(credential->GetSerialization(&response, &serialized, &text, &icon)));
    require(state->result == Code::cancelled && !state->request && !state->submitted && serialized.rgbSerialization == nullptr);
    state->request = std::make_unique<SecretRequest>();
    require(SUCCEEDED(credential->SetDeselected()));
    require(!state->request && state->result == Code::cancelled);
    require(SUCCEEDED(credential->ReportResult(static_cast<NTSTATUS>(0xc000006d), static_cast<NTSTATUS>(0xc0000071), &text, &icon)));
    require(state->result == Code::password_expired && icon == CPSI_ERROR);
    CoTaskMemFree(text); text = nullptr;
    require(SUCCEEDED(credential->ReportResult(static_cast<NTSTATUS>(0xc000006d), 0, &text, &icon)));
    require(state->result == Code::authentication_failed);
    CoTaskMemFree(text);
    credential->Release();
    auto* provider = new Provider();
    require(provider->SetUsageScenario(CPUS_CREDUI, 0) == E_NOTIMPL);
    require(provider->SetUsageScenario(CPUS_CHANGE_PASSWORD, 0) == E_NOTIMPL);
    require(provider->SetSerialization(nullptr) == E_NOTIMPL);
    require(SUCCEEDED(provider->UnAdvise()));
    require(SUCCEEDED(provider->UnAdvise()));
    provider->Release();
    require(DllCanUnloadNow() == S_OK);
    CoUninitialize();
    std::printf("%u provider checks passed; no OS authentication, registration or unlock attempted.\n", count);
    return 0;
}
