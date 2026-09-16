/** Compatibility reads forward to Engine; Admin never issues or stores tokens. */
import { engineClientIdentity, engineIdentity, EngineIdentityError } from "@/lib/server/engine-identity";
import { getSessionIdentifier, type AdminUserRecord } from "@/lib/users";

export type MobileAuthUser = AdminUserRecord & { email: string };
export type MobileTokenPair = { accessToken: string; accessTokenExpiresAt: string; refreshToken: string; refreshTokenExpiresAt: string; user: MobileAuthUser; deviceId: string };
export type MobileDeviceSession = { id: string; deviceName: string; createdAt: string; expiresAt: string; lastUsedAt?: string; active: boolean };

export function resolveMobileBearerToken(req: Request) {
    const match = /^Bearer\s+(.+)$/i.exec(req.headers.get("authorization") || "");
    return match?.[1]?.trim() || null;
}

export async function resolveMobileAccessUser(req: Request): Promise<AdminUserRecord | null> {
    const token = resolveMobileBearerToken(req);
    if (!token) return null;
    try {
        return (await engineClientIdentity<{ user: AdminUserRecord }>("/auth/me", { headers: { authorization: `Bearer ${token}` } })).user;
    } catch (error) {
        if (error instanceof EngineIdentityError && error.status === 401) return null;
        throw error;
    }
}

export async function rotateMobileSession(refreshToken: string, deviceName?: string, rotationId?: string) {
    try {
        return await engineClientIdentity<MobileTokenPair>("/auth/refresh", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refreshToken, deviceName, rotationId }) });
    } catch (error) {
        if (error instanceof EngineIdentityError && error.status === 401) return null;
        throw error;
    }
}

export async function revokeMobileRefreshToken(refreshToken: string) {
    return (await engineClientIdentity<{ revoked: boolean }>("/auth/logout", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refreshToken }) })).revoked;
}

export async function listMobileDeviceSessions(_userId: string) {
    return (await engineIdentity<{ devices: MobileDeviceSession[] }>("/devices")).devices;
}

export async function revokeMobileDeviceSession(_userId: string, deviceSessionId: string) {
    return (await engineIdentity<{ revoked: boolean }>(`/devices/${encodeURIComponent(deviceSessionId)}`, { method: "DELETE" })).revoked;
}

export function mobileAuthUserResponse(user: AdminUserRecord) {
    return { ...user, email: user.email || user.login, sessionIdentifier: getSessionIdentifier(user) };
}
