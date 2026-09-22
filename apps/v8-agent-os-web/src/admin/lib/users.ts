import { engineIdentity, EngineIdentityError } from "@admin/lib/server/engine-identity";
import type { BackgroundPlaylist } from "@v8/product-ui/background-playlist";

export type AdminUserRole = "ADMIN" | "USER";
export type UserAppearancePreferences = {
    webBackground?: BackgroundPlaylist;
    lightBackgroundMedia?: string;
    lightBackgroundMediaType?: "image" | "video";
    lightBackgroundImage?: string;
    lightBackgroundEnabled?: boolean;
};
export type AdminUserRecord = {
    id: string; login: string; sessionIdentifier?: string; email?: string; name?: string | null;
    role: AdminUserRole; image?: string; appearance?: UserAppearancePreferences;
    mustChangePassword?: boolean; createdAt?: string; updatedAt?: string;
};
export const PERSONAL_OWNER_MODE = true;
export const MAX_NON_ADMIN_USERS = 0;

export function isOwnerAlreadyInitializedError(error: unknown) {
    return error instanceof EngineIdentityError && error.code === "owner_already_initialized";
}

export async function listUsers(): Promise<AdminUserRecord[]> {
    return (await engineIdentity<{ users: AdminUserRecord[] }>("/users")).users;
}

export async function hasOwner(): Promise<boolean> {
    const state = await engineIdentity<{ initialized: boolean; needsSetup?: boolean }>("/owner");
    return state.initialized && !state.needsSetup;
}

export async function findUserById(id: string): Promise<AdminUserRecord | null> {
    return (await listUsers()).find(user => user.id === id) || null;
}

export async function findUserByIdentifier(identifier: string): Promise<AdminUserRecord | null> {
    const value = identifier.trim().toLowerCase();
    if (!value) return null;
    return (await listUsers()).find(user => [user.login, user.email, user.sessionIdentifier].some(item => item?.toLowerCase() === value)) || null;
}

export async function createUserRecord(input: { login: string; name?: string; role?: AdminUserRole; password?: string }) {
    if (input.role !== "ADMIN") throw new Error("Only the instance Owner can be created.");
    return (await engineIdentity<{ user: AdminUserRecord }>("/bootstrap", { method: "POST", body: JSON.stringify(input) })).user;
}

export async function updateUserRecord(id: string, patch: Partial<Pick<AdminUserRecord, "name" | "image" | "appearance" | "email">>) {
    const owner = await engineIdentity<{ user: AdminUserRecord }>("/owner");
    if (owner.user?.id !== id) throw new EngineIdentityError("owner_unavailable", 403);
    return (await engineIdentity<{ user: AdminUserRecord }>("/profile", { method: "PATCH", body: JSON.stringify(patch) })).user;
}

export function getSessionIdentifier(user: AdminUserRecord) {
    return user.sessionIdentifier || user.email || user.login;
}
