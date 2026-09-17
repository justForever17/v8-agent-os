import { Platform } from "react-native";
import { requireOptionalNativeModule } from "expo-modules-core";

export type ExecutorState = {
    supported: boolean;
    enabled: boolean;
    connected: boolean;
    accessibilityGranted: boolean;
    notificationGranted: boolean;
    androidApi?: number;
    fullDisplayCapture: boolean;
    windowCaptureAvailable: boolean;
    gestureAvailable: boolean;
    gestureUnavailableReason?: string;
    authorityId?: string | null;
    profileAuthorityKey?: string | null;
    deviceId?: string | null;
    baseUrl?: string | null;
    name?: string | null;
    allowedApps: string[];
    grantRevision: number;
    status: string;
    lastError: string;
    recentReceipts: Array<{ commandId: string; status: string; error?: string }>;
};
type NativeExecutor = {
    getState(): Promise<ExecutorState>;
    enroll(ticket: string, authorityId: string, baseUrl: string, name: string, profileAuthorityKey: string, apps: string[]): Promise<ExecutorState>;
    setAllowedApps(apps: string[]): Promise<ExecutorState>;
    setFullDisplayCapture(enabled: boolean): Promise<ExecutorState>;
    acknowledgeGrantRevision(revision: number): Promise<ExecutorState>;
    enable(): Promise<ExecutorState>;
    stop(): Promise<ExecutorState>;
    disable(): Promise<ExecutorState>;
    revoke(): Promise<ExecutorState>;
    forgetProfile(profileAuthorityKey: string): Promise<ExecutorState>;
    openAccessibilitySettings(): Promise<void>;
    addListener(name: "onState", listener: (state: ExecutorState) => void): { remove(): void };
};
const native = Platform.OS === "android" ? requireOptionalNativeModule<NativeExecutor>("V8DeviceExecutor") : null;
const unsupported: ExecutorState = {
    supported: false, enabled: false, connected: false, accessibilityGranted: false, notificationGranted: false,
    fullDisplayCapture: false, windowCaptureAvailable: false, gestureAvailable: false,
    allowedApps: [], grantRevision: 0, status: "unsupported", lastError: "native_build_required", recentReceipts: [],
};
const required = () => { if (!native) throw new Error("native_build_required"); return native; };

export const deviceExecutor = {
    getState: () => native?.getState() ?? Promise.resolve(unsupported),
    subscribe: (listener: (state: ExecutorState) => void) => native?.addListener("onState", listener) ?? { remove() {} },
    enroll: (...args: Parameters<NativeExecutor["enroll"]>) => required().enroll(...args),
    setAllowedApps: (apps: string[]) => required().setAllowedApps(apps),
    setFullDisplayCapture: (enabled: boolean) => required().setFullDisplayCapture(enabled),
    acknowledgeGrantRevision: (revision: number) => required().acknowledgeGrantRevision(revision),
    enable: () => required().enable(),
    stop: () => native?.stop() ?? Promise.resolve(unsupported),
    disable: () => native?.disable() ?? Promise.resolve(unsupported),
    revoke: () => required().revoke(),
    openAccessibilitySettings: () => required().openAccessibilitySettings(),
    // Call only for explicit logout/forget. Chat activation and token refresh must not call this.
    forgetProfile: (authorityKey: string) => native?.forgetProfile(authorityKey) ?? Promise.resolve(unsupported),
};

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;
async function managementJson(fetcher: AuthorizedFetch, path: string, body: unknown, method = "POST") {
    const response = await fetcher(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) {
        try {
            // PhoneTransport owns the permit until the body is consumed or cancelled.
            // Keep Expo's lazy body getter untouched on this finite response path.
            await response.text();
        } catch { /* Preserve the HTTP failure if body cleanup also fails. */ }
        throw new Error(`executor_management_${response.status}`);
    }
    return response.json();
}
export async function enrollExecutor(fetcher: AuthorizedFetch, input: { baseUrl: string; name: string; authorityKey: string; allowedApps: string[] }) {
    const url = new URL(input.baseUrl);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("https_origin_required");
    const ticket = await managementJson(fetcher, "/api/client/executors/tickets", { deviceClass: "android", name: input.name, baseUrl: url.origin });
    if (ticket.baseUrl !== url.origin) throw new Error("enrollment_origin_changed");
    // Only a one-use ticket crosses JS. The returned long-lived credential stays native.
    return deviceExecutor.enroll(ticket.ticket, ticket.authorityId, ticket.baseUrl, input.name, input.authorityKey, input.allowedApps);
}
export async function updateExecutorGrants(fetcher: AuthorizedFetch, state: ExecutorState, authorityKey: string, allowedApps: string[]) {
    if (!state.deviceId || state.profileAuthorityKey !== authorityKey) throw new Error("select_bound_chat_profile");
    await deviceExecutor.setAllowedApps(allowedApps);
    const result = await managementJson(fetcher, `/api/client/executors/${encodeURIComponent(state.deviceId)}/grants`, {
        expectedRevision: state.grantRevision || 1,
        grants: executorGrants(state, allowedApps),
    }, "PUT");
    return deviceExecutor.acknowledgeGrantRevision(result.grantRevision);
}

export function executorGrants(state: ExecutorState, allowedApps: string[]) {
    const capabilities = ["android.observe", "android.action"];
    if (state.windowCaptureAvailable || state.fullDisplayCapture) capabilities.push("android.capture");
    const grants = allowedApps.flatMap(resourceId => capabilities.map(capability => ({ capability, resourceId })));
    if (state.fullDisplayCapture) grants.push({ capability: "android.capture", resourceId: "display" });
    return grants;
}
