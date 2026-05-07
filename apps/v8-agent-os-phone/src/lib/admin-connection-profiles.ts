import { normalizeAdminBaseUrl } from "@/src/lib/admin-client";
import { getStoredValue, setStoredValue } from "@/src/lib/mobile-storage";
import type { ConnectionSummary } from "@/src/types/admin";

export type AdminConnectionProfile = {
    id: string;
    label: string;
    adminBaseUrl: string;
    adminApiBaseUrl?: string;
    bridgeMode?: string;
    transportKind?: string;
    transportProfileId?: string;
    reachable?: boolean;
    version?: string;
    lastUsedAt: string;
};

function createProfileId() {
    const cryptoApi = (globalThis as typeof globalThis & { crypto?: { randomUUID?: () => string } }).crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
        return cryptoApi.randomUUID();
    }
    return `admin-connection-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveLabel(adminBaseUrl: string) {
    try {
        const url = new URL(adminBaseUrl);
        return url.host || adminBaseUrl;
    } catch {
        return adminBaseUrl.replace(/^https?:\/\//i, "") || adminBaseUrl;
    }
}

function sanitizeProfile(value: unknown): AdminConnectionProfile | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Record<string, unknown>;
    const adminBaseUrl = normalizeAdminBaseUrl(String(record.adminBaseUrl || ""));
    if (!adminBaseUrl) {
        return null;
    }
    return {
        id: String(record.id || createProfileId()),
        label: String(record.label || deriveLabel(adminBaseUrl)),
        adminBaseUrl,
        adminApiBaseUrl: typeof record.adminApiBaseUrl === "string" ? record.adminApiBaseUrl : "",
        bridgeMode: typeof record.bridgeMode === "string" ? record.bridgeMode : "",
        transportKind: typeof record.transportKind === "string" ? record.transportKind : "",
        transportProfileId: typeof record.transportProfileId === "string" ? record.transportProfileId : "",
        reachable: typeof record.reachable === "boolean" ? record.reachable : undefined,
        version: typeof record.version === "string" ? record.version : "",
        lastUsedAt: String(record.lastUsedAt || new Date(0).toISOString()),
    };
}

export async function readAdminConnectionProfiles() {
    try {
        const raw = await getStoredValue("adminConnectionProfiles");
        const parsed = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) {
            return [] as AdminConnectionProfile[];
        }
        return parsed
            .map((item) => sanitizeProfile(item))
            .filter((item): item is AdminConnectionProfile => Boolean(item))
            .sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));
    } catch {
        return [] as AdminConnectionProfile[];
    }
}

export async function writeAdminConnectionProfiles(profiles: AdminConnectionProfile[]) {
    await setStoredValue("adminConnectionProfiles", JSON.stringify(profiles));
}

export async function readActiveAdminConnectionProfileId() {
    return getStoredValue("activeAdminConnectionProfileId");
}

export async function writeActiveAdminConnectionProfileId(profileId: string | null | undefined) {
    await setStoredValue("activeAdminConnectionProfileId", profileId ? String(profileId) : "");
}

export function upsertAdminConnectionProfile(
    profiles: AdminConnectionProfile[],
    input: {
        adminBaseUrl: string;
        label?: string | null;
        profileId?: string | null;
        summary?: ConnectionSummary | null;
    },
) {
    const adminBaseUrl = normalizeAdminBaseUrl(input.adminBaseUrl);
    if (!adminBaseUrl) {
        return { profile: null, profiles };
    }
    const summaryConnection = input.summary?.connection || {};
    const existing = profiles.find((profile) =>
        (input.profileId && profile.id === input.profileId)
        || normalizeAdminBaseUrl(profile.adminBaseUrl) === adminBaseUrl,
    );
    const summaryRecord = (input.summary || {}) as Record<string, unknown>;
    const summaryConnectionRecord = summaryConnection as Record<string, unknown>;
    const version = typeof summaryRecord.version === "string"
        ? summaryRecord.version
        : typeof summaryConnectionRecord.version === "string"
            ? summaryConnectionRecord.version
            : existing?.version || "";
    const profile: AdminConnectionProfile = {
        id: existing?.id || input.profileId || createProfileId(),
        label: input.label || existing?.label || deriveLabel(adminBaseUrl),
        adminBaseUrl,
        adminApiBaseUrl: summaryConnection.adminApiBaseUrl || summaryConnection.configuredAdminApiBaseUrl || existing?.adminApiBaseUrl || "",
        bridgeMode: summaryConnection.bridgeMode || existing?.bridgeMode || "",
        transportKind: summaryConnection.transportKind || existing?.transportKind || "",
        transportProfileId: summaryConnection.transportProfileId || existing?.transportProfileId || "",
        reachable: typeof summaryConnection.reachable === "boolean" ? summaryConnection.reachable : existing?.reachable,
        version,
        lastUsedAt: new Date().toISOString(),
    };
    const nextProfiles = [
        profile,
        ...profiles.filter((item) => item.id !== profile.id),
    ].sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));
    return { profile, profiles: nextProfiles };
}

export function removeAdminConnectionProfile(profiles: AdminConnectionProfile[], profileId: string) {
    return profiles.filter((profile) => profile.id !== profileId);
}
