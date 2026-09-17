export type DistributionFetch = (path: string, init?: RequestInit) => Promise<Response>;
export type DistributionRole = { id: string; label: string };
export type DistributionTemplate = {
    id: string; label: string; description: string; values: Record<string, unknown>; roles?: DistributionRole[];
};
export type DistributionPeer = {
    linkId: string; peerId: string; displayName: string; online: boolean; localRole: string; remoteRole: string;
};
export type DistributionCapabilities = {
    peerId: string; protocolVersion: number; roles: DistributionRole[];
    models: { modelRef: string; label: string; ready: boolean; missingRequirements: string[] }[];
    pathPolicy: string;
};
export type DistributionMapping = { roles: Record<string, string>; models: Record<string, string> };
export type DistributionTarget = {
    linkId: string; peerId: string; displayName: string; state: string;
    diff: { field: string; before: unknown; after: unknown }[];
    missingRequirements: string[]; errorCode?: string | null;
    receipt?: { transactionId: string; state: string; readback?: unknown } | null;
};
export type DistributionJob = {
    jobId: string; revision: number; planDigest: string; state: string; templateId: string;
    targets: DistributionTarget[]; createdAt: string; updatedAt: string;
};
export type DistributionCatalog = {
    servingInstanceId: string; templates: DistributionTemplate[]; peers: DistributionPeer[]; jobs: DistributionJob[];
};
export type DistributionAction = "prepare" | "confirm" | "retry" | "cancel" | "withdraw";
const ROOT = "/api/client/config-distribution";

export class DistributionError extends Error {
    constructor(public readonly code: string, public readonly status: number) { super(code); }
}
async function request<T>(fetcher: DistributionFetch, path: string, signal?: AbortSignal, body?: unknown, method = "POST"): Promise<T> {
    const response = await fetcher(path, body === undefined ? { signal } : {
        method, signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
    if (!response.ok) throw new DistributionError(typeof payload.error === "string" ? payload.error : typeof payload.detail === "string" ? payload.detail : "distribution_unavailable", response.status);
    return payload as T;
}
export async function loadDistribution(fetcher: DistributionFetch, instanceId: string, signal?: AbortSignal) {
    const payload = await request<DistributionCatalog>(fetcher, ROOT, signal);
    if (!instanceId || payload.servingInstanceId !== instanceId) throw new DistributionError("instance_changed", 409);
    return payload;
}
export async function loadDistributionTarget(fetcher: DistributionFetch, peer: DistributionPeer, signal?: AbortSignal) {
    const payload = await request<DistributionCapabilities>(fetcher, `${ROOT}/targets/${encodeURIComponent(peer.linkId)}`, signal);
    if (payload.peerId !== peer.peerId) throw new DistributionError("peer_changed", 409);
    if (payload.protocolVersion !== 1 || payload.pathPolicy !== "target_local_only") throw new DistributionError("unsupported_protocol", 409);
    return payload;
}
export function createDistribution(fetcher: DistributionFetch, input: {
    commandId: string; templateId: string; targets: { linkId: string; mapping: DistributionMapping }[];
}, signal?: AbortSignal) {
    return request<DistributionJob>(fetcher, ROOT, signal, input);
}
export async function loadDistributionJob(fetcher: DistributionFetch, jobId: string, signal?: AbortSignal) {
    const payload = await request<DistributionJob>(fetcher, `${ROOT}/${encodeURIComponent(jobId)}`, signal);
    if (payload.jobId !== jobId) throw new DistributionError("job_changed", 409);
    return payload;
}
export async function actOnDistribution(fetcher: DistributionFetch, job: DistributionJob, action: DistributionAction, commandId: string, signal?: AbortSignal) {
    const payload = await request<DistributionJob>(fetcher, `${ROOT}/${encodeURIComponent(job.jobId)}/${action}`, signal,
        { commandId, revision: job.revision, planDigest: job.planDigest });
    if (payload.jobId !== job.jobId) throw new DistributionError("job_changed", 409);
    return payload;
}
export function setDistributionRole(fetcher: DistributionFetch, peer: DistributionPeer, localRole: "primary" | "companion", signal?: AbortSignal) {
    return request(fetcher, `/api/client/supervisor-peers/${encodeURIComponent(peer.linkId)}`, signal, { localRole }, "PATCH");
}

export function distributionCommandId() {
    return `phone-distribution-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
export function distributionPlanKey(job: DistributionJob) {
    return `${job.jobId}:${job.revision}:${job.planDigest}`;
}
export function distributionMappingReady(template: DistributionTemplate, mapping: DistributionMapping, capabilities?: DistributionCapabilities) {
    if (template.id !== "model-roles") return true;
    if (!capabilities) return false;
    const roles = template.roles || [];
    return roles.length > 0 && roles.every(({ id }) => {
        const role = mapping.roles[id];
        const model = capabilities.models.find((item) => item.modelRef === mapping.models[id]);
        return capabilities.roles.some((item) => item.id === role) && model?.ready === true && !model.missingRequirements.length;
    }) && new Set(roles.map(({ id }) => mapping.roles[id])).size === roles.length;
}
export function distributionValue(value: unknown) {
    if (value === undefined || value === null) return "—";
    return typeof value === "string" ? value : JSON.stringify(value);
}
