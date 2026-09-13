/** HTTP-only peer ingress. Engine owns peer signatures, tokens and pairing codes. */
const PEER_POST_PATHS = new Set([
    "peer/join", "peer/challenge", "peer/wake", "peer/delegations", "peer/neighbors/pairing/consume",
    "peer/neighbors/messages", "peer/neighbors/tasks",
]);
const BODY_LIMIT = 262_144;
const RESPONSE_LIMIT = 1_048_576;

async function boundedBytes(body: ReadableStream<Uint8Array> | null, limit: number, signal: AbortSignal): Promise<Uint8Array> {
    signal.throwIfAborted();
    if (!body) return new Uint8Array();
    const reader = body.getReader();
    const abort = () => { void reader.cancel().catch(() => undefined); };
    signal.addEventListener("abort", abort, { once: true });
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > limit) throw new RangeError("body_too_large");
            chunks.push(value);
        }
        signal.throwIfAborted();
    } finally { signal.removeEventListener("abort", abort); await reader.cancel().catch(() => undefined); reader.releaseLock(); }
    const result = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
    return result;
}

export async function proxyNetworkPeer(request: Request, segments: string[], engineBaseUrl: string,
    fetcher: typeof fetch = fetch): Promise<Response> {
    const suffix = segments.join("/");
    if (suffix === "peer/ws") return Response.json({ detail: "admin_peer_websocket_unsupported" }, { status: 426 });
    if (!PEER_POST_PATHS.has(suffix) || segments.some(part => !/^[a-z-]+$/.test(part))) {
        return Response.json({ detail: "peer_route_not_exposed" }, { status: 404 });
    }
    if (request.method !== "POST") return Response.json({ detail: "method_not_allowed" }, { status: 405, headers: { Allow: "POST" } });
    if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
        return Response.json({ detail: "json_required" }, { status: 415 });
    }
    if (Number(request.headers.get("content-length") || 0) > BODY_LIMIT) return Response.json({ detail: "peer_body_too_large" }, { status: 413 });
    const headers = new Headers({ "Content-Type": "application/json" });
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(25_000)]);
    const token = request.headers.get("x-v8-peer-token");
    if (token) headers.set("X-V8-Peer-Token", token);
    // No browser cookie, Admin credential or client-supplied target is forwarded.
    let body: Uint8Array;
    try { body = await boundedBytes(request.body, BODY_LIMIT, signal); }
    catch { return Response.json({ detail: "peer_body_too_large_or_unreadable" }, { status: signal.aborted ? 408 : 413 }); }
    try {
        const upstream = await fetcher(`${engineBaseUrl.replace(/\/$/, "")}/network-supervisor/${suffix}`, {
            method: "POST", headers, body: Buffer.from(body), cache: "no-store", redirect: "error",
            signal,
        });
        const bytes = await boundedBytes(upstream.body, RESPONSE_LIMIT, signal);
        return new Response(Buffer.from(bytes), { status: upstream.status, headers: {
            "Content-Type": upstream.headers.get("content-type") || "application/json",
            "Cache-Control": "no-store",
        } });
    } catch {
        return Response.json({ detail: "peer_transport_unavailable" }, { status: 502 });
    }
}
