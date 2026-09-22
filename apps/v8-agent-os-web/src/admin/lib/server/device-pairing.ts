import { engineIdentity } from "@admin/lib/server/engine-identity";
import type { AdminUserRecord } from "@admin/lib/users";

export type DeviceSurface = "phone";
export type DevicePairingTicket = { pairingId: string; instanceId: string; surface: "phone"; adminBaseUrl: string; pairingCode: string; pairingUri: string; expiresAt: string };

export async function createDevicePairingTicket(input: { owner: AdminUserRecord; surface?: unknown; adminBaseUrl: string; deviceName?: string; ttlMs?: number }): Promise<DevicePairingTicket> {
    if (input.owner.role !== "ADMIN") throw new Error("owner_admin_required");
    if (input.surface && input.surface !== "phone") throw new Error("phone_pairing_only");
    return engineIdentity<DevicePairingTicket>("/pairing-ticket", { method: "POST", body: JSON.stringify({ surface: "phone", adminBaseUrl: input.adminBaseUrl, deviceName: input.deviceName, ttlMs: input.ttlMs }) });
}

export async function revokeDevicePairingTicket(id: string) {
    return engineIdentity<{ revoked: boolean }>(`/pairing-ticket/${encodeURIComponent(id)}`, { method: "DELETE" });
}
