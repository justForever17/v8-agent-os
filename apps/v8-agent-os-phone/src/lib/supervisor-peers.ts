type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

export type SupervisorPeer = {
    linkId: string; peerId: string; servingInstanceId: string; sessionId: string;
    sessionKind: "local_neighbor"; displayName: string; online: boolean; lastSeenAt: string;
    localRole: string; remoteRole: string; trustStatus: string; description: string;
};
export type PeerMessage = { id: string; seq: number; body: string; direction: string; status: string; createdAt: string; fromNickname: string };

async function json<T>(fetcher: AuthorizedFetch, path: string, init?: RequestInit): Promise<T> {
    const response = await fetcher(path, init);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The Supervisor connection is unavailable.");
    return payload;
}

export function listSupervisorPeers(fetcher: AuthorizedFetch, options: { cursor?: string; query?: string; signal?: AbortSignal } = {}) {
    const query = new URLSearchParams({ limit: "40" });
    if (options.cursor) query.set("cursor", options.cursor);
    if (options.query) query.set("q", options.query);
    return json<{ items: SupervisorPeer[]; nextCursor: string | null; servingInstanceId: string }>(fetcher, `/api/client/supervisor-peers?${query}`, { signal: options.signal });
}
export function loadPeerTimeline(fetcher: AuthorizedFetch, peer: SupervisorPeer, signal?: AbortSignal, before?: string) {
    return json<{ peer: SupervisorPeer; items: PeerMessage[]; nextCursor?: string; previousCursor?: string }>(fetcher,
        `/api/client/supervisor-peers/${encodeURIComponent(peer.linkId)}/timeline${before ? `?before=${encodeURIComponent(before)}` : ""}`, { signal });
}
export function updateSupervisorPeer(fetcher: AuthorizedFetch, peer: SupervisorPeer, nickname: string) {
    return json(fetcher, `/api/client/supervisor-peers/${encodeURIComponent(peer.linkId)}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ remoteNickname: nickname }) });
}
export function revokeSupervisorPeer(fetcher: AuthorizedFetch, peer: SupervisorPeer) {
    return json(fetcher, `/api/client/supervisor-peers/${encodeURIComponent(peer.linkId)}`, { method: "DELETE" });
}
