export type TimelineItem = { messageId: string; seq: number };

/** Refresh mutable delivery state in place while retaining explicitly loaded history. */
export function mergeNeighborTimeline<T extends TimelineItem>(current: T[], incoming: T[]): T[] {
    const messages = new Map(current.map(item => [item.messageId, item]));
    for (const item of incoming) messages.set(item.messageId, item);
    return Array.from(messages.values()).sort((left, right) => left.seq - right.seq);
}
