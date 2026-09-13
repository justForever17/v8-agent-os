import { fetch as expoFetch } from "expo/fetch";
import { buildAdminApiUrl, parseJsonSafe, streamSse } from "@/src/lib/admin-client";
import type { PhoneUser } from "@/src/types/admin";
import type { ProfileCredentials } from "@/src/lib/admin-connection-profiles";

export function abortError() { return new DOMException("The view or connection changed", "AbortError"); }

export class PhoneTransport {
    private disposed = false;
    private controllers = new Set<AbortController>();
    private refreshInFlight: Promise<void> | null = null;
    private streams = new Map<string, AbortController>();
    private activeReads = 0;
    private waitingReads: Array<() => void> = [];
    private credentials: ProfileCredentials;
    public endpoint: string;

    constructor(private readonly options: {
        endpoints: string[];
        credentials: ProfileCredentials;
        principalId: string;
        native: boolean;
        persistRefresh: (credentials: ProfileCredentials, user: PhoneUser) => Promise<void>;
        onEndpoint: (endpoint: string) => void;
        onClock: (header: string | null) => void;
    }) {
        this.credentials = options.credentials;
        this.endpoint = options.endpoints[0];
    }

    assertCurrent(signal?: AbortSignal | null) {
        if (this.disposed || signal?.aborted) throw abortError();
    }
    dispose() {
        this.disposed = true;
        for (const controller of this.controllers) controller.abort();
        this.controllers.clear();
        for (const release of this.waitingReads.splice(0)) release();
    }
    stopStreams() {
        for (const controller of this.streams.values()) controller.abort();
        this.streams.clear();
    }
    async settleRefresh() {
        // A token rotation already in flight must persist before another profile
        // disposes this connection. It never refreshes an inactive profile itself.
        await this.refreshInFlight?.catch(() => undefined);
    }
    private controller(signal?: AbortSignal | null) {
        this.assertCurrent(signal);
        const controller = new AbortController();
        const abort = () => controller.abort();
        signal?.addEventListener("abort", abort, { once: true });
        this.controllers.add(controller);
        return { controller, release: () => {
            signal?.removeEventListener("abort", abort);
            this.controllers.delete(controller);
        } };
    }
    private async fetch(endpoint: string, path: string, init: RequestInit, timeout: number) {
        const { controller, release } = this.controller(init.signal);
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(buildAdminApiUrl(endpoint, path), { ...init, signal: controller.signal });
            this.assertCurrent(init.signal);
            // Keep cancellation alive through body consumption. A response header
            // alone must not release an old authority's pending JSON read.
            const cleanup = () => { clearTimeout(timer); release(); };
            for (const method of ["json", "text", "blob", "arrayBuffer"] as const) {
                const original = response[method].bind(response);
                Object.defineProperty(response, method, { value: async () => {
                    try {
                        const value = await original();
                        this.assertCurrent(init.signal);
                        return value;
                    } finally { cleanup(); }
                } });
            }
            this.options.onClock(response.headers.get("x-v8-engine-now"));
            return response;
        } catch (error) { clearTimeout(timer); release(); throw error; }
    }
    private refresh = async () => {
        if (this.refreshInFlight) return this.refreshInFlight;
        const request = (async () => {
            this.assertCurrent();
            const response = await this.fetch(this.endpoint, "/api/client/auth/refresh", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ refreshToken: this.credentials.refreshToken, deviceName: "v8-phone" }),
            }, 6_000);
            if (!response.ok) throw new Error("This connection needs pairing again.");
            const payload = await parseJsonSafe<ProfileCredentials & { user: PhoneUser }>(response);
            this.assertCurrent();
            if (!payload?.accessToken || !payload.refreshToken || payload.user?.id !== this.options.principalId) {
                throw new Error("The paired account changed. Pair this connection again.");
            }
            await this.options.persistRefresh(payload, payload.user);
            this.assertCurrent();
            this.credentials = { accessToken: payload.accessToken, refreshToken: payload.refreshToken };
        })();
        this.refreshInFlight = request;
        try { await request; } finally { if (this.refreshInFlight === request) this.refreshInFlight = null; }
    };

    authorizedFetch = async (path: string, init: RequestInit = {}): Promise<Response> => {
        this.assertCurrent(init.signal);
        const read = !init.method || ["GET", "HEAD"].includes(init.method.toUpperCase());
        if (read) {
            while (this.activeReads >= 2) {
                await new Promise<void>((resolve) => this.waitingReads.push(resolve));
                this.assertCurrent(init.signal);
            }
            this.activeReads += 1;
        }
        try {
            const deadline = Date.now() + 10_000;
            // Only pairing-bound aliases. Never race or replay a side effect after
            // an ambiguous network failure; only an explicit 401 permits retry.
            const candidates = [this.endpoint, ...this.options.endpoints.filter((item) => item !== this.endpoint)].slice(0, read ? 2 : 1);
            let lastError: unknown;
            for (const endpoint of candidates) {
                this.assertCurrent(init.signal);
                if (Date.now() >= deadline) break;
                try {
                    const token = this.credentials.accessToken;
                    const headers = new Headers(init.headers);
                    headers.set("Authorization", `Bearer ${token}`);
                    let response = await this.fetch(endpoint, path, { ...init, headers }, Math.min(read ? 4_000 : 10_000, deadline - Date.now()));
                    if (response.status === 401) {
                        await response.text();
                        // Concurrent 401s received with the old token share one refresh.
                        if (this.credentials.accessToken === token) await this.refresh();
                        this.assertCurrent(init.signal);
                        headers.set("Authorization", `Bearer ${this.credentials.accessToken}`);
                        response = await this.fetch(endpoint, path, { ...init, headers }, Math.max(1, deadline - Date.now()));
                    }
                    if (response.ok && endpoint !== this.endpoint) {
                        this.endpoint = endpoint;
                        this.options.onEndpoint(endpoint);
                    }
                    return response;
                } catch (error) {
                    this.assertCurrent(init.signal);
                    lastError = error;
                    if (!read) throw error;
                }
            }
            throw lastError || new Error("The connection is offline. Retry when it is reachable.");
        } finally {
            if (read) { this.activeReads -= 1; this.waitingReads.shift()?.(); }
        }
    };

    authorizedRealtimeStream = async (path: string, onEvent: (name: string, payload: unknown) => void, signal?: AbortSignal) => {
        const lane = path.includes("session-activity") ? "summary" : "detail";
        this.streams.get(lane)?.abort();
        const { controller, release } = this.controller(signal);
        this.streams.set(lane, controller);
        const receive = (name: string, payload: unknown) => {
            if (!this.disposed && !controller.signal.aborted) onEvent(name, payload);
        };
        const open = async () => {
            const headers = { Authorization: `Bearer ${this.credentials.accessToken}`, Accept: "text/event-stream" };
            // Native fetch consumes byte chunks; RN XHR retains the whole SSE
            // responseText for the lifetime of this (potentially endless) stream.
            const streamFetch = this.options.native ? expoFetch : fetch;
            const response = await streamFetch(buildAdminApiUrl(this.endpoint, path), { headers, signal: controller.signal });
            if (this.disposed || controller.signal.aborted || !response.ok) {
                await response.body?.cancel().catch(() => undefined);
                this.assertCurrent(controller.signal);
                throw Object.assign(new Error("Realtime connection unavailable"), { status: response.status });
            }
            this.options.onClock(response.headers.get("x-v8-engine-now"));
            await streamSse(response, receive, controller.signal);
        };
        try {
            const token = this.credentials.accessToken;
            try { await open(); } catch (error) {
                this.assertCurrent(controller.signal);
                if ((error as { status?: number }).status !== 401) throw error;
                if (this.credentials.accessToken === token) await this.refresh();
                this.assertCurrent(controller.signal);
                await open();
            }
        } finally {
            release();
            if (this.streams.get(lane) === controller) this.streams.delete(lane);
        }
    };
}
