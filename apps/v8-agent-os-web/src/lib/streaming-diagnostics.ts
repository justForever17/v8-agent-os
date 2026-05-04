type RuntimeEventLike = {
    type?: unknown;
    content?: unknown;
    run_id?: unknown;
    runId?: unknown;
    data?: unknown;
};

export type StreamLatencyStats = {
    count: number;
    deltaChars: number[];
    interDeltaMs: number[];
    proxyLagMs: number[];
    clientCommitLagMs: number[];
    renderLagMs: number[];
    firstProviderDeltaAtMs?: number;
    firstClientReceiveAtMs?: number;
    lastClientReceiveAtMs?: number;
};

export type PendingStreamDiagnostic = {
    streamMetricKey: string;
    clientReceivedAtMs: number;
};

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

export function readStreamDiagnostics(value: unknown) {
    return asRecord(asRecord(value)._diagnostics);
}

export function toEpochMs(value: unknown) {
    if (typeof value === "number" && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === "string" && value.trim()) {
        const parsed = Date.parse(value);
        return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
}

function percentile(values: number[], percentileValue: number) {
    const sorted = values.filter((item) => Number.isFinite(item)).sort((a, b) => a - b);
    if (!sorted.length) {
        return 0;
    }
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((percentileValue / 100) * sorted.length) - 1));
    return Math.round(sorted[index]);
}

function summarizeStreamLatencyStats(stats: StreamLatencyStats) {
    const firstTokenMs = stats.firstProviderDeltaAtMs !== undefined && stats.firstClientReceiveAtMs !== undefined
        ? Math.max(0, Math.round(stats.firstClientReceiveAtMs - stats.firstProviderDeltaAtMs))
        : 0;
    return {
        count: stats.count,
        firstTokenMs,
        interDeltaP50: percentile(stats.interDeltaMs, 50),
        interDeltaP95: percentile(stats.interDeltaMs, 95),
        deltaCharsP50: percentile(stats.deltaChars, 50),
        deltaCharsP95: percentile(stats.deltaChars, 95),
        proxyLagP95: percentile(stats.proxyLagMs, 95),
        clientCommitLagP95: percentile(stats.clientCommitLagMs, 95),
        renderLagP95: percentile(stats.renderLagMs, 95),
    };
}

export function recordReceivedStreamDelta({
    surface,
    event,
    diagnostics,
    receivedAtMs,
    statsByKey,
}: {
    surface: string;
    event: RuntimeEventLike;
    diagnostics: Record<string, unknown>;
    receivedAtMs: number;
    statsByKey: Map<string, StreamLatencyStats>;
}): PendingStreamDiagnostic | null {
    const eventType = String(event.type || "").trim();
    if (eventType !== "text_chunk" && eventType !== "reasoning_chunk") {
        return null;
    }
    const data = asRecord(event.data);
    const runId = String(event.run_id || event.runId || diagnostics.runId || "unknown-run").trim();
    const transport = String(diagnostics.transport || data.transport || "unknown-transport").trim();
    const streamMetricKey = `${runId}:${transport}`;
    const stats = statsByKey.get(streamMetricKey) || {
        count: 0,
        deltaChars: [],
        interDeltaMs: [],
        proxyLagMs: [],
        clientCommitLagMs: [],
        renderLagMs: [],
    };
    const providerDeltaAtMs = toEpochMs(diagnostics.providerDeltaAtMs) ?? toEpochMs(diagnostics.providerDeltaAt);
    const proxyFlushAtMs = toEpochMs(diagnostics.proxyFlushAtMs) ?? toEpochMs(diagnostics.proxyFlushAt);
    if (stats.firstProviderDeltaAtMs === undefined && providerDeltaAtMs !== undefined) {
        stats.firstProviderDeltaAtMs = providerDeltaAtMs;
    }
    if (stats.firstClientReceiveAtMs === undefined) {
        stats.firstClientReceiveAtMs = receivedAtMs;
    }
    if (stats.lastClientReceiveAtMs !== undefined) {
        stats.interDeltaMs.push(Math.max(0, receivedAtMs - stats.lastClientReceiveAtMs));
    }
    if (proxyFlushAtMs !== undefined) {
        stats.proxyLagMs.push(Math.max(0, receivedAtMs - proxyFlushAtMs));
    }
    stats.deltaChars.push(Number(diagnostics.deltaChars || String(event.content || "").length) || 0);
    stats.lastClientReceiveAtMs = receivedAtMs;
    stats.count += 1;
    statsByKey.set(streamMetricKey, stats);
    if (process.env.NODE_ENV !== "production" && stats.count % 20 === 0) {
        try {
            console.debug(`[${surface}/stream-summary]`, {
                streamMetricKey,
                ...summarizeStreamLatencyStats(stats),
            });
        } catch {
            // ignore dev diagnostics failures
        }
    }
    return { streamMetricKey, clientReceivedAtMs: receivedAtMs };
}

export function markStreamClientCommit(
    statsByKey: Map<string, StreamLatencyStats>,
    pending: PendingStreamDiagnostic | null,
    committedAtMs: number,
) {
    if (!pending) {
        return;
    }
    const stats = statsByKey.get(pending.streamMetricKey);
    if (!stats) {
        return;
    }
    stats.clientCommitLagMs.push(Math.max(0, committedAtMs - pending.clientReceivedAtMs));
}

export function markStreamClientRender(
    statsByKey: Map<string, StreamLatencyStats>,
    pending: PendingStreamDiagnostic | null,
    renderedAtMs: number,
) {
    if (!pending) {
        return;
    }
    const stats = statsByKey.get(pending.streamMetricKey);
    if (!stats) {
        return;
    }
    stats.renderLagMs.push(Math.max(0, renderedAtMs - pending.clientReceivedAtMs));
}
