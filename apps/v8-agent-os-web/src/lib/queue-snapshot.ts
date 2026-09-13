export function reconcileQueueSnapshot<T extends { id: string }>(current: T[], incoming: T[] | null, sequence: number, knownSequence: number, complete: boolean) {
    if (incoming === null || sequence < knownSequence) return current;
    if (complete) return incoming;
    const next = new Map(current.map((item) => [item.id, item]));
    for (const item of incoming) next.set(item.id, item);
    return [...next.values()];
}

export async function readCompleteQueue<T extends { id: string }>(sessionId: string, fetchPage: (afterOrdinal: number | null) => Promise<{ sessionId: string; latestSeq: number; queuedMessages: T[]; queuedMessagesWindow: { hasMore: boolean; nextOrdinal: number | null } }>, stillCurrent: (sequence: number) => boolean) {
    // Restart once when a concurrent mutation changes the snapshot sequence.
    for (let attempt = 0; attempt < 2; attempt++) {
        let cursor: number | null = null;
        let sequence: number | undefined;
        const items = new Map<string, T>();
        for (let page = 0; page < 128; page++) {
            const response = await fetchPage(cursor);
            if (response.sessionId !== sessionId || !stillCurrent(response.latestSeq)) throw new Error("Queue owner or sequence changed");
            if (sequence !== undefined && response.latestSeq !== sequence) break;
            sequence = response.latestSeq;
            for (const item of response.queuedMessages) items.set(item.id, item);
            if (!response.queuedMessagesWindow.hasMore) return { items: [...items.values()], sequence };
            const next = response.queuedMessagesWindow.nextOrdinal;
            if (next === null || next <= (cursor ?? -1)) throw new Error("Invalid queue cursor");
            cursor = next;
        }
    }
    throw new Error("Queue is changing; sync again");
}
