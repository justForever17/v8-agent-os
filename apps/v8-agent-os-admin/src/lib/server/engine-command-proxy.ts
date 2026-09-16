import { NextRequest, NextResponse } from "next/server";
import { engineFetch } from "@/lib/server/engine-fetch";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_COMMAND_TIMEOUT_MS = 15_000;
type CommandTarget = { approvalId?: string; runId?: string; command: string };

/** A deadline bounds the HTTP exchange, not the Engine operation. Never replay it. */
export async function proxyEngineCommand(req: NextRequest, path: string, target: CommandTarget, headers: HeadersInit) {
    let payload: unknown;
    try { payload = await req.json(); } catch {
        return NextResponse.json({ error: "Invalid command JSON", unknownOutcome: false }, { status: 400 });
    }
    const deadline = new AbortController();
    const timer = setTimeout(() => deadline.abort(new DOMException("Engine command timed out", "TimeoutError")), ENGINE_COMMAND_TIMEOUT_MS);
    const signal = AbortSignal.any([req.signal, deadline.signal]);
    let dispatched = false;
    let response: Response | undefined;
    try {
        signal.throwIfAborted();
        dispatched = true;
        response = await engineFetch(`${resolveEngineBaseUrl()}${path}`, {
            method: "POST", headers, body: JSON.stringify(payload), cache: "no-store", signal,
        });
        // A truncated body after 200 is not a successful decision receipt.
        const data: unknown = await response.json();
        signal.throwIfAborted();
        if (response.ok && (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).length === 0)) {
            throw new Error("Invalid Engine command receipt");
        }
        return NextResponse.json(data, { status: response.status });
    } catch {
        const code = deadline.signal.aborted ? "engine_timeout"
            : req.signal.aborted ? "engine_request_aborted"
            : response ? "engine_command_response_invalid" : "engine_unavailable";
        const status = deadline.signal.aborted ? 504 : req.signal.aborted ? 499
            : response && !response.ok ? response.status : 502;
        return NextResponse.json({
            error: dispatched
                ? "The command outcome is unknown. Refresh its state before deciding the next action."
                : "The request was cancelled before forwarding.",
            code, unknownOutcome: dispatched, retryable: false, recovery: "resync", ...target,
        }, { status });
    } finally {
        clearTimeout(timer);
    }
}
