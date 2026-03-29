import { createClientId } from "@/lib/id";
import {
    AdminConnection,
    deriveAdminConnectionLabel,
    normalizeAdminBaseUrl,
} from "@/lib/admin-connection-utils";

const CONNECTION_PROFILES_KEY = "v8-agent-os_admin_connections";
const ACTIVE_CONNECTION_ID_KEY = "v8-agent-os_active_admin_connection_id";

export type AdminConnectionProfile = AdminConnection & {
    id: string;
    label: string;
    lastUsedAt: string;
};

function isBrowser() {
    return typeof window !== "undefined";
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
        id: String(record.id || createClientId("admin-connection")),
        label: String(record.label || deriveAdminConnectionLabel(adminBaseUrl)),
        adminBaseUrl,
        adminApiBaseUrl: String(record.adminApiBaseUrl || ""),
        bridgeMode: String(record.bridgeMode || ""),
        reachable: typeof record.reachable === "boolean" ? record.reachable : undefined,
        version: String(record.version || ""),
        lastUsedAt: String(record.lastUsedAt || new Date(0).toISOString()),
    };
}

export function readAdminConnectionProfiles() {
    if (!isBrowser()) {
        return [] as AdminConnectionProfile[];
    }
    try {
        const raw = window.localStorage.getItem(CONNECTION_PROFILES_KEY);
        if (!raw) {
            return [] as AdminConnectionProfile[];
        }
        const parsed = JSON.parse(raw);
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

export function writeAdminConnectionProfiles(profiles: AdminConnectionProfile[]) {
    if (!isBrowser()) return;
    window.localStorage.setItem(CONNECTION_PROFILES_KEY, JSON.stringify(profiles));
}

export function readActiveAdminConnectionProfileId() {
    if (!isBrowser()) return null;
    const raw = window.localStorage.getItem(ACTIVE_CONNECTION_ID_KEY);
    return raw ? String(raw) : null;
}

export function writeActiveAdminConnectionProfileId(profileId: string | null) {
    if (!isBrowser()) return;
    if (!profileId) {
        window.localStorage.removeItem(ACTIVE_CONNECTION_ID_KEY);
        return;
    }
    window.localStorage.setItem(ACTIVE_CONNECTION_ID_KEY, profileId);
}

export function upsertAdminConnectionProfile(
    profiles: AdminConnectionProfile[],
    connection: AdminConnection,
    options?: { profileId?: string | null; label?: string | null },
) {
    const normalizedAdminBaseUrl = normalizeAdminBaseUrl(connection.adminBaseUrl);
    const existing = profiles.find((profile) => {
        if (options?.profileId && profile.id === options.profileId) {
            return true;
        }
        return normalizeAdminBaseUrl(profile.adminBaseUrl) === normalizedAdminBaseUrl;
    });

    const profile: AdminConnectionProfile = {
        id: existing?.id || options?.profileId || createClientId("admin-connection"),
        label: String(options?.label || existing?.label || deriveAdminConnectionLabel(normalizedAdminBaseUrl)),
        adminBaseUrl: normalizedAdminBaseUrl,
        adminApiBaseUrl: connection.adminApiBaseUrl || "",
        bridgeMode: connection.bridgeMode || "",
        reachable: connection.reachable,
        version: connection.version || "",
        lastUsedAt: new Date().toISOString(),
    };

    const nextProfiles = [
        profile,
        ...profiles.filter((item) => item.id !== profile.id),
    ].sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));

    return {
        profile,
        profiles: nextProfiles,
    };
}

export function removeAdminConnectionProfile(profiles: AdminConnectionProfile[], profileId: string) {
    return profiles.filter((profile) => profile.id !== profileId);
}

export function findAdminConnectionProfileById(profiles: AdminConnectionProfile[], profileId: string | null | undefined) {
    if (!profileId) return null;
    return profiles.find((profile) => profile.id === profileId) || null;
}

export function findAdminConnectionProfileByBaseUrl(
    profiles: AdminConnectionProfile[],
    adminBaseUrl: string | null | undefined,
) {
    const normalized = normalizeAdminBaseUrl(String(adminBaseUrl || ""));
    if (!normalized) return null;
    return profiles.find((profile) => normalizeAdminBaseUrl(profile.adminBaseUrl) === normalized) || null;
}
