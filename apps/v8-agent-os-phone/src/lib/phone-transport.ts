import { fetch as expoFetch } from "expo/fetch";
import { buildAdminApiUrl, parseJsonSafe, streamSse } from "@/src/lib/admin-client";
import type { PhoneUser } from "@/src/types/admin";
import type { ProfileCredentials } from "@/src/lib/admin-connection-profiles";

export function abortError() { return new DOMException("The view or connection changed", "AbortError"); }

function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
    if (signal.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
        const abort = () => reject(abortError());
        signal.addEventListener("abort", abort, { once: true });
        operation.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
    });
}

export class PhoneTransport {
    private disposed = false;
    private controllers = new Set<AbortController>();
    private refreshInFlight: Promise<void> | null = null;
    private streams = new Map<string, AbortController>();
    private activeReads = 0;
    private waitingReads: Array<() => void> = [];
    private verifications = new Map<string, { promise: Promise<void>; controller: AbortController; users: number }>();
    private responseFinished = new WeakMap<Response, (callback: () => void) => void>();
    private foreground = false;
    private recoveryTimer?: ReturnType<typeof setTimeout>;
    private recoveryController?: AbortController;
    private recoveryInFlight: Promise<boolean> | null = null;
    private credentials: ProfileCredentials;
    public endpoint: string;

    constructor(private readonly options: {
        endpoints: string[];
        localEndpoints?: string[];
        instanceId: string;
        credentials: ProfileCredentials;
        principalId: string;
        native: boolean;
        persistRefresh: (credentials: ProfileCredentials, user: PhoneUser) => Promise<void>;
        onEndpoint: (endpoint: string) => void;
        onClock: (header: string | null) => void;
    }) {
        if (!options.instanceId) throw new Error("The saved connection has no verified instance. Pair it again.");
        this.credentials = options.credentials;
        this.endpoint = options.endpoints[0];
    }

    assertCurrent(signal?: AbortSignal | null) {
        if (this.disposed || signal?.aborted) throw abortError();
    }
    dispose() {
        this.disposed = true;
        this.setForeground(false);
        for (const controller of this.controllers) controller.abort();
        this.controllers.clear();
        for (const wake of this.waitingReads.splice(0)) wake();
    }
    stopStreams() {
        for (const controller of this.streams.values()) controller.abort();
        this.streams.clear();
    }
    async settleRefresh() { await this.refreshInFlight?.catch(() => undefined); }

    private controller(signal?: AbortSignal | null, timeout?: number) {
        this.assertCurrent(signal);
        const controller = new AbortController();
        const abort = () => controller.abort();
        const timer = timeout === undefined ? undefined : setTimeout(abort, Math.max(1, timeout));
        signal?.addEventListener("abort", abort, { once: true });
        this.controllers.add(controller);
        return { controller, release: () => {
            clearTimeout(timer);
            signal?.removeEventListener("abort", abort);
            this.controllers.delete(controller);
        } };
    }

    // Permits cover finite requests, including verification/recovery. Streaming
    // bodies have their own two lanes. No profile outside this owner is probed.
    private async acquire(signal: AbortSignal) {
        while (this.activeReads >= 2) {
            await new Promise<void>((resolve, reject) => {
                const cleanup = () => {
                    signal.removeEventListener("abort", abort);
                    this.waitingReads = this.waitingReads.filter(item => item !== wake);
                };
                const wake = () => { cleanup(); resolve(); };
                const abort = () => { cleanup(); reject(abortError()); };
                this.waitingReads.push(wake);
                signal.addEventListener("abort", abort, { once: true });
                if (signal.aborted) abort();
            });
        }
        this.assertCurrent(signal);
        this.activeReads += 1;
        let released = false;
        return () => {
            if (released) return;
            released = true;
            this.activeReads -= 1;
            this.waitingReads.shift()?.();
        };
    }

    private async fetch(endpoint: string, path: string, init: RequestInit, timeout: number) {
        const { controller, release } = this.controller(init.signal, timeout);
        const signal = controller.signal;
        let response: Response | undefined;
        let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
        let finished = false;
        const listeners: Array<() => void> = [];
        const complete = () => {
            if (finished) return;
            finished = true;
            signal.removeEventListener("abort", abort);
            release();
            for (const listener of listeners.splice(0)) listener();
        };
        const cancelBody = () => {
            const operation = reader ? reader.cancel() : response?.body?.cancel();
            void operation?.catch(() => undefined);
        };
        const abort = () => { cancelBody(); complete(); };
        signal.addEventListener("abort", abort, { once: true });
        try {
            // React Native's global XHR-based fetch ignores redirect:error.
            // Expo implements it natively for finite requests as well as SSE.
            const fetcher = this.options.native ? expoFetch : fetch;
            const request = fetcher(buildAdminApiUrl(endpoint, path), {
                ...init, credentials: "omit", redirect: "error", signal,
            });
            void request.then(late => { if (signal.aborted) void late.body?.cancel().catch(() => undefined); }, () => {});
            response = await abortable(request, signal);
            this.assertCurrent(signal);
            const guard = () => this.assertCurrent(signal);
            const originalResponse = response;
            let wrappedBody: ReadableStream<Uint8Array> | null | undefined;
            const body = () => {
                if (wrappedBody !== undefined) return wrappedBody;
                const raw = originalResponse.body;
                wrappedBody = raw ? new Proxy(raw, { get: (target, key) => {
                    if (key === "cancel") return async (reason?: unknown) => {
                        try { return await target.cancel(reason); } finally { controller.abort(); complete(); }
                    };
                    if (key === "getReader") return () => {
                        guard();
                        reader = target.getReader();
                        const ownedReader = reader;
                        return new Proxy(ownedReader, { get: (item, member) => {
                            if (member === "read") return async () => {
                                try {
                                    guard();
                                    const value = await abortable(item.read(), signal);
                                    guard();
                                    if (value.done) complete();
                                    return value;
                                } catch (error) { controller.abort(); complete(); throw error; }
                            };
                            if (member === "cancel") return async (reason?: unknown) => {
                                try { return await item.cancel(reason); } finally { controller.abort(); complete(); }
                            };
                            const value = Reflect.get(item, member, item);
                            return typeof value === "function" ? value.bind(item) : value;
                        } });
                    };
                    const value = Reflect.get(target, key, target);
                    return typeof value === "function" ? value.bind(target) : value;
                } }) : null;
                return wrappedBody;
            };
            const wrapped = new Proxy(originalResponse, { get: (target, key) => {
                if (key === "body") return body();
                if (["json", "text", "blob", "arrayBuffer", "formData", "bytes"].includes(String(key))) {
                    const method = Reflect.get(target, key, target);
                    if (typeof method === "function") return async () => {
                        try { guard(); const value = await abortable(method.call(target), signal); guard(); return value; }
                        catch (error) { controller.abort(); throw error; }
                        finally { complete(); }
                    };
                }
                const value = Reflect.get(target, key, target);
                return typeof value === "function" ? value.bind(target) : value;
            } });
            this.responseFinished.set(wrapped, callback => { if (finished) callback(); else listeners.push(callback); });
            // Expo's body getter starts streaming and drains its text/json sink.
            // Never touch it until the caller actually chooses reader mode.
            if (init.method === "HEAD" || [204, 205, 304].includes(originalResponse.status)) complete();
            return wrapped;
        } catch (error) { complete(); throw error; }
    }

    private async verifyEndpoint(endpoint: string, signal: AbortSignal) {
        this.assertCurrent(signal);
        let flight = this.verifications.get(endpoint);
        if (!flight) {
            const scope = this.controller(undefined, 1_800);
            const promise = (async () => {
                const response = await this.fetch(endpoint, "/api/client/instance", { cache: "no-store", signal: scope.controller.signal }, 1_800);
                if (!response.ok) { await response.text(); throw new Error("The saved instance is unavailable. No credentials were sent."); }
                const payload = await parseJsonSafe<{ instanceId?: string }>(response);
                if (!payload?.instanceId) throw new Error("The instance identity could not be verified. No credentials were sent.");
                if (payload?.instanceId !== this.options.instanceId) throw new Error("The address belongs to a different instance. No credentials were sent.");
            })().finally(() => {
                scope.release();
                if (this.verifications.get(endpoint)?.promise === promise) this.verifications.delete(endpoint);
            });
            flight = { promise, controller: scope.controller, users: 0 };
            this.verifications.set(endpoint, flight);
        }
        flight.users += 1;
        try { await abortable(flight.promise, signal); this.assertCurrent(signal); }
        finally { if (--flight.users === 0) flight.controller.abort(); }
    }

    private refresh = async (endpoint: string, signal: AbortSignal) => {
        if (this.refreshInFlight) return abortable(this.refreshInFlight, signal);
        const request = (async () => {
            await this.verifyEndpoint(endpoint, signal);
            const response = await this.fetch(endpoint, "/api/client/auth/refresh", {
                method: "POST", headers: { "Content-Type": "application/json" }, signal,
                body: JSON.stringify({ refreshToken: this.credentials.refreshToken, deviceName: "v8-phone" }),
            }, 6_000);
            if (!response.ok) { await response.text(); throw new Error("This connection needs pairing again."); }
            const payload = await parseJsonSafe<ProfileCredentials & { user: PhoneUser }>(response);
            this.assertCurrent(signal);
            if (!payload?.accessToken || !payload.refreshToken || payload.user?.id !== this.options.principalId) {
                throw new Error("The paired account changed. Pair this connection again.");
            }
            await this.options.persistRefresh(payload, payload.user);
            this.assertCurrent(signal);
            this.credentials = { accessToken: payload.accessToken, refreshToken: payload.refreshToken };
        })();
        this.refreshInFlight = request;
        try { await request; } finally { if (this.refreshInFlight === request) this.refreshInFlight = null; }
    };

    private async requestEndpoint(endpoint: string, path: string, init: RequestInit, signal: AbortSignal, deadline: number) {
        await this.verifyEndpoint(endpoint, signal);
        const token = this.credentials.accessToken;
        const headers = new Headers(init.headers);
        headers.set("Authorization", `Bearer ${token}`);
        const read = !init.method || ["GET", "HEAD"].includes(init.method.toUpperCase());
        let response = await this.fetch(endpoint, path, { ...init, headers, signal }, Math.min(read ? 4_000 : 10_000, deadline - Date.now()));
        if (response.status === 401) {
            const authStage = response.headers.get("x-v8-auth-stage");
            await response.text();
            // The BFF auth owner marks rejection before forwarding the action.
            // HTTP status alone does not establish whether a write was accepted.
            if (!read && authStage !== "pre_execution") {
                throw Object.assign(new Error("The request outcome could not be confirmed."), {
                    status: 401, acceptanceUnknown: true,
                });
            }
            if (this.credentials.accessToken === token) await this.refresh(endpoint, signal);
            await this.verifyEndpoint(endpoint, signal);
            headers.set("Authorization", `Bearer ${this.credentials.accessToken}`);
            response = await this.fetch(endpoint, path, { ...init, headers, signal }, deadline - Date.now());
            if (!read && response.status === 401 && response.headers.get("x-v8-auth-stage") !== "pre_execution") {
                await response.text();
                throw Object.assign(new Error("The request outcome could not be confirmed."), {
                    status: 401, acceptanceUnknown: true,
                });
            }
        }
        this.options.onClock(response.headers.get("x-v8-engine-now"));
        return response;
    }

    authorizedFetch = async (path: string, init: RequestInit = {}): Promise<Response> => {
        const scope = this.controller(init.signal, 10_000);
        const signal = scope.controller.signal;
        const deadline = Date.now() + 10_000;
        let releasePermit: (() => void) | undefined;
        let transferred = false;
        const complete = () => { signal.removeEventListener("abort", complete); scope.release(); releasePermit?.(); };
        signal.addEventListener("abort", complete, { once: true });
        try {
            releasePermit = await this.acquire(signal);
            this.assertCurrent(signal);
            const read = !init.method || ["GET", "HEAD"].includes(init.method.toUpperCase());
            const candidates = [this.endpoint, ...this.options.endpoints.filter(item => item !== this.endpoint)].slice(0, read ? 2 : 1);
            let lastError: unknown;
            for (const endpoint of candidates) {
                this.assertCurrent(signal);
                try {
                    const response = await this.requestEndpoint(endpoint, path, init, signal, deadline);
                    if (response.ok && endpoint !== this.endpoint) { this.endpoint = endpoint; this.options.onEndpoint(endpoint); }
                    transferred = true;
                    this.responseFinished.get(response)!(complete);
                    return response;
                } catch (error) {
                    this.assertCurrent(signal); lastError = error;
                    if (!read) throw error; // Never replay ambiguous writes to an alias.
                }
            }
            throw lastError || new Error("The connection is offline. Retry when it is reachable.");
        } finally { if (!transferred) complete(); }
    };

    setForeground(visible: boolean) {
        this.foreground = visible && !this.disposed;
        clearTimeout(this.recoveryTimer);
        if (!this.foreground) { this.recoveryController?.abort(); return; }
        void this.recoverLocalEndpoint();
    }
    recoverLocalEndpoint = (): Promise<boolean> => {
        if (!this.foreground || this.disposed) return Promise.resolve(false);
        if (this.recoveryInFlight) return this.recoveryInFlight;
        clearTimeout(this.recoveryTimer);
        const candidates = (this.options.localEndpoints || []).filter(value => value !== this.endpoint).slice(0, 2);
        if (!candidates.length || this.options.localEndpoints?.includes(this.endpoint)) return Promise.resolve(false);
        const scope = this.controller(undefined, 6_000);
        this.recoveryController = scope.controller;
        const signal = scope.controller.signal;
        const request = (async () => {
            const releasePermit = await this.acquire(signal);
            try {
                for (const endpoint of candidates) {
                    this.assertCurrent(signal);
                    try {
                        const response = await this.requestEndpoint(endpoint, "/api/client/connection", {}, signal, Date.now() + 4_000);
                        const payload = response.ok ? await parseJsonSafe<{ user?: PhoneUser; linkManifest?: { instanceId?: string } }>(response) : (await response.text(), null);
                        this.assertCurrent(signal);
                        if (payload?.user?.id !== this.options.principalId || payload.linkManifest?.instanceId !== this.options.instanceId) continue;
                        this.endpoint = endpoint; this.options.onEndpoint(endpoint);
                        return true;
                    } catch { this.assertCurrent(signal); }
                }
                return false;
            } finally { releasePermit(); }
        })().catch(() => false).finally(() => {
            scope.release();
            this.recoveryController = undefined;
            this.recoveryInFlight = null;
            if (this.foreground && !this.disposed && !this.options.localEndpoints?.includes(this.endpoint)) {
                this.recoveryTimer = setTimeout(() => { void this.recoverLocalEndpoint(); }, 60_000);
            }
        });
        this.recoveryInFlight = request;
        return request;
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
            const endpoint = this.endpoint;
            const releasePermit = await this.acquire(controller.signal);
            try { await this.verifyEndpoint(endpoint, controller.signal); } finally { releasePermit(); }
            const headers = { Authorization: `Bearer ${this.credentials.accessToken}`, Accept: "text/event-stream" };
            const streamFetch = this.options.native ? expoFetch : fetch;
            const response = await streamFetch(buildAdminApiUrl(endpoint, path), { headers, signal: controller.signal, credentials: "omit", redirect: "error" });
            if (this.disposed || controller.signal.aborted || !response.ok) {
                await response.body?.cancel().catch(() => undefined);
                this.assertCurrent(controller.signal);
                throw Object.assign(new Error("Realtime connection unavailable"), { status: response.status, endpoint });
            }
            this.options.onClock(response.headers.get("x-v8-engine-now"));
            await streamSse(response, receive, controller.signal);
        };
        try {
            const token = this.credentials.accessToken;
            try { await open(); } catch (error) {
                this.assertCurrent(controller.signal);
                if ((error as { status?: number }).status !== 401) throw error;
                if (this.credentials.accessToken === token) {
                    const releasePermit = await this.acquire(controller.signal);
                    try { await this.refresh((error as { endpoint: string }).endpoint, controller.signal); } finally { releasePermit(); }
                }
                this.assertCurrent(controller.signal);
                await open();
            }
        } finally {
            release();
            if (this.streams.get(lane) === controller) this.streams.delete(lane);
        }
    };
}
