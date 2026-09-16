function pause(ms: number, signal: AbortSignal) {
    return new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
    });
}

/** Retry only the idempotent readiness read; never repeat terminal creation or input. */
export async function readTerminalInstance(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<string> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
        let response: Response;
        try {
            response = await fetcher("/api/client/instance", { cache: "no-store", signal });
        } catch (error) {
            if (signal.aborted) throw error;
            if (attempt === 2) throw new Error("web.terminal.identityFailed");
            await pause(250 * 2 ** attempt, signal);
            continue;
        }
        if (response.ok) {
            const identity = await response.json().catch(() => null);
            if (typeof identity?.instanceId === "string" && identity.instanceId.trim()) return identity.instanceId;
            throw new Error("web.terminal.identityFailed");
        }
        if (![429, 502, 503, 504].includes(response.status) || attempt === 2) throw new Error("web.terminal.identityFailed");
        await pause(250 * 2 ** attempt, signal);
    }
    throw new Error("web.terminal.identityFailed");
}
