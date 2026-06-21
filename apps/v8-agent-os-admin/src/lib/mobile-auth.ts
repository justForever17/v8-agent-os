import crypto from "crypto";

import { verifyPassword } from "@/lib/password";
import { readJson, writeJson } from "@/lib/storage";
import { findUserById, findUserByIdentifier, getSessionIdentifier, type AdminUserRecord } from "@/lib/users";

const MOBILE_AUTH_CONFIG_FILE = "mobile_app_auth.json";
const MOBILE_REFRESH_TOKENS_FILE = "mobile_app_tokens.json";
const ACCESS_TOKEN_TTL_SECONDS = 24 * 60 * 60;
const REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60;

type MobileAuthConfig = {
    version: 1;
    secret: string;
};

type MobileRefreshTokenRecord = {
    id: string;
    userId: string;
    sessionIdentifier: string;
    tokenHash: string;
    createdAt: string;
    expiresAt: string;
    lastUsedAt?: string;
    revokedAt?: string;
    deviceName?: string;
};

type MobileRefreshTokenStore = {
    version: 1;
    refreshTokens: MobileRefreshTokenRecord[];
};

type MobileAccessClaims = {
    type: "mobile_access";
    sub: string;
    sid: string;
    login: string;
    role: string;
    did?: string;
    iat: number;
    exp: number;
};

export type MobileAuthUser = {
    id: string;
    email: string;
    login: string;
    name?: string | null;
    image?: string;
    role: string;
    mustChangePassword: boolean;
};

export type MobileCredentialCheckResult =
    | { ok: true; user: AdminUserRecord }
    | { ok: false; reason: "missing_credentials" | "user_not_found" | "invalid_password" };

type MobileTokenPair = {
    accessToken: string;
    accessTokenExpiresAt: string;
    refreshToken: string;
    refreshTokenExpiresAt: string;
    user: MobileAuthUser;
};

export type MobileDeviceSession = {
    id: string;
    deviceName: string;
    createdAt: string;
    expiresAt: string;
    lastUsedAt?: string;
    active: boolean;
};

function base64UrlEncode(value: string | Buffer) {
    return Buffer.from(value).toString("base64url");
}

function base64UrlDecode(value: string) {
    return Buffer.from(value, "base64url").toString("utf-8");
}

function readMobileAuthConfig(): MobileAuthConfig {
    const existing = readJson<Partial<MobileAuthConfig>>(MOBILE_AUTH_CONFIG_FILE, { version: 1 });
    const secret = String(existing.secret || "").trim();
    if (secret) {
        return { version: 1, secret };
    }

    const created: MobileAuthConfig = {
        version: 1,
        secret: crypto.randomBytes(48).toString("base64url"),
    };
    writeJson(MOBILE_AUTH_CONFIG_FILE, created);
    return created;
}

function readRefreshTokenStore(): MobileRefreshTokenStore {
    const existing = readJson<Partial<MobileRefreshTokenStore>>(MOBILE_REFRESH_TOKENS_FILE, { version: 1, refreshTokens: [] });
    return {
        version: 1,
        refreshTokens: Array.isArray(existing.refreshTokens) ? existing.refreshTokens : [],
    };
}

function writeRefreshTokenStore(store: MobileRefreshTokenStore) {
    writeJson(MOBILE_REFRESH_TOKENS_FILE, {
        version: 1,
        refreshTokens: store.refreshTokens,
    });
}

function signMobileToken(header: Record<string, unknown>, payload: MobileAccessClaims) {
    const secret = readMobileAuthConfig().secret;
    const encodedHeader = base64UrlEncode(JSON.stringify(header));
    const encodedPayload = base64UrlEncode(JSON.stringify(payload));
    const signature = crypto
        .createHmac("sha256", secret)
        .update(`${encodedHeader}.${encodedPayload}`)
        .digest("base64url");
    return `${encodedHeader}.${encodedPayload}.${signature}`;
}

function hashRefreshToken(token: string) {
    return crypto
        .createHash("sha256")
        .update(`${readMobileAuthConfig().secret}:${token}`)
        .digest("hex");
}

function sanitizeMobileAuthUser(user: AdminUserRecord): MobileAuthUser {
    return {
        id: user.id,
        email: getSessionIdentifier(user),
        login: user.login,
        name: user.name,
        image: user.image,
        role: user.role,
        mustChangePassword: Boolean(user.mustChangePassword),
    };
}

function createAccessToken(user: AdminUserRecord, deviceSessionId?: string) {
    const now = Math.floor(Date.now() / 1000);
    const claims: MobileAccessClaims = {
        type: "mobile_access",
        sub: user.id,
        sid: getSessionIdentifier(user),
        login: user.login,
        role: user.role,
        did: deviceSessionId,
        iat: now,
        exp: now + ACCESS_TOKEN_TTL_SECONDS,
    };
    return {
        token: signMobileToken({ alg: "HS256", typ: "JWT" }, claims),
        expiresAt: new Date((claims.exp) * 1000).toISOString(),
    };
}

function createRefreshTokenRecord(user: AdminUserRecord, deviceName?: string) {
    const token = crypto.randomBytes(48).toString("base64url");
    const createdAt = new Date().toISOString();
    const expiresAt = new Date(Date.now() + REFRESH_TOKEN_TTL_SECONDS * 1000).toISOString();
    const record: MobileRefreshTokenRecord = {
        id: crypto.randomUUID(),
        userId: user.id,
        sessionIdentifier: getSessionIdentifier(user),
        tokenHash: hashRefreshToken(token),
        createdAt,
        expiresAt,
        deviceName: String(deviceName || "").trim() || undefined,
    };
    return { token, record };
}

function issueMobileTokenPair(user: AdminUserRecord, deviceName?: string): MobileTokenPair {
    const refresh = createRefreshTokenRecord(user, deviceName);
    const access = createAccessToken(user, refresh.record.id);
    const store = readRefreshTokenStore();
    store.refreshTokens = store.refreshTokens.filter((record) => {
        if (record.userId !== user.id) return true;
        return !record.revokedAt && new Date(record.expiresAt).getTime() > Date.now();
    });
    store.refreshTokens.push(refresh.record);
    writeRefreshTokenStore(store);

    return {
        accessToken: access.token,
        accessTokenExpiresAt: access.expiresAt,
        refreshToken: refresh.token,
        refreshTokenExpiresAt: refresh.record.expiresAt,
        user: sanitizeMobileAuthUser(user),
    };
}

export function issueMobileSessionForUser(user: AdminUserRecord, deviceName?: string) {
    return issueMobileTokenPair(user, deviceName);
}

function verifyAccessToken(token: string): MobileAccessClaims | null {
    const parts = String(token || "").trim().split(".");
    if (parts.length !== 3) {
        return null;
    }
    const [encodedHeader, encodedPayload, signature] = parts;
    if (!encodedHeader || !encodedPayload || !signature) {
        return null;
    }

    const expectedSignature = crypto
        .createHmac("sha256", readMobileAuthConfig().secret)
        .update(`${encodedHeader}.${encodedPayload}`)
        .digest("base64url");

    const signatureBuffer = Buffer.from(signature);
    const expectedBuffer = Buffer.from(expectedSignature);
    if (signatureBuffer.length !== expectedBuffer.length || !crypto.timingSafeEqual(signatureBuffer, expectedBuffer)) {
        return null;
    }

    try {
        const payload = JSON.parse(base64UrlDecode(encodedPayload)) as MobileAccessClaims;
        if (payload.type !== "mobile_access") {
            return null;
        }
        if (payload.exp * 1000 <= Date.now()) {
            return null;
        }
        return payload;
    } catch {
        return null;
    }
}

export function resolveMobileBearerToken(req: Request) {
    const header = req.headers.get("authorization");
    if (!header || !header.toLowerCase().startsWith("bearer ")) {
        return null;
    }
    return header.slice(7).trim() || null;
}

export async function resolveMobileAccessUser(req: Request): Promise<AdminUserRecord | null> {
    const token = resolveMobileBearerToken(req);
    if (!token) {
        return null;
    }
    const payload = verifyAccessToken(token);
    if (!payload) {
        return null;
    }
    const user = findUserById(payload.sub);
    if (!user) {
        return null;
    }
    const sessionIdentifier = getSessionIdentifier(user);
    if (sessionIdentifier !== payload.sid) {
        return null;
    }
    if (payload.did) {
        const store = readRefreshTokenStore();
        const deviceSession = store.refreshTokens.find((record) => record.id === payload.did);
        if (!deviceSession || deviceSession.revokedAt || new Date(deviceSession.expiresAt).getTime() <= Date.now()) {
            return null;
        }
    }
    return user;
}

export async function verifyMobileCredentials(login: string, password: string): Promise<MobileCredentialCheckResult> {
    const identifier = String(login || "").trim();
    if (!identifier || !password) {
        return { ok: false, reason: "missing_credentials" };
    }
    const user = findUserByIdentifier(identifier);
    if (!user) {
        return { ok: false, reason: "user_not_found" };
    }
    if (user.role !== "ADMIN") {
        return { ok: false, reason: "user_not_found" };
    }
    if (!user.password) {
        return { ok: false, reason: "invalid_password" };
    }
    const isValid = await verifyPassword(password, user.password);
    if (!isValid) {
        return { ok: false, reason: "invalid_password" };
    }
    return { ok: true, user };
}

export async function createMobileSession(login: string, password: string, deviceName?: string) {
    const result = await verifyMobileCredentials(login, password);
    if (!result.ok) {
        return null;
    }
    return issueMobileTokenPair(result.user, deviceName);
}

export async function rotateMobileSession(refreshToken: string, deviceName?: string) {
    const tokenHash = hashRefreshToken(String(refreshToken || "").trim());
    const store = readRefreshTokenStore();
    const record = store.refreshTokens.find((item) => item.tokenHash === tokenHash);
    if (!record || record.revokedAt || new Date(record.expiresAt).getTime() <= Date.now()) {
        return null;
    }

    const user = findUserById(record.userId);
    if (!user || user.role !== "ADMIN" || getSessionIdentifier(user) !== record.sessionIdentifier) {
        return null;
    }

    record.revokedAt = new Date().toISOString();
    record.lastUsedAt = record.revokedAt;
    writeRefreshTokenStore(store);

    return issueMobileTokenPair(user, deviceName);
}

export function revokeMobileRefreshToken(refreshToken: string) {
    const tokenHash = hashRefreshToken(String(refreshToken || "").trim());
    const store = readRefreshTokenStore();
    let changed = false;
    for (const record of store.refreshTokens) {
        if (record.tokenHash === tokenHash && !record.revokedAt) {
            record.revokedAt = new Date().toISOString();
            changed = true;
        }
    }
    if (changed) {
        writeRefreshTokenStore(store);
    }
    return changed;
}

export function listMobileDeviceSessions(userId: string): MobileDeviceSession[] {
    const now = Date.now();
    return readRefreshTokenStore().refreshTokens
        .filter((record) => record.userId === userId && !record.revokedAt && new Date(record.expiresAt).getTime() > now)
        .map((record) => ({
            id: record.id,
            deviceName: record.deviceName || "V8 client",
            createdAt: record.createdAt,
            expiresAt: record.expiresAt,
            lastUsedAt: record.lastUsedAt,
            active: true,
        }))
        .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime());
}

export function revokeMobileDeviceSession(userId: string, deviceSessionId: string) {
    const store = readRefreshTokenStore();
    const record = store.refreshTokens.find((item) => item.id === deviceSessionId && item.userId === userId);
    if (!record || record.revokedAt) {
        return false;
    }
    record.revokedAt = new Date().toISOString();
    writeRefreshTokenStore(store);
    return true;
}

export function mobileAuthUserResponse(user: AdminUserRecord) {
    return sanitizeMobileAuthUser(user);
}
