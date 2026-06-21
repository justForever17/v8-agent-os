"use server";

import { auth } from "@/lib/auth";
import { resolveAdminApiBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

async function createAdminRequest(path: string, init: RequestInit = {}) {
    const session = await auth();
    const userIdentifier = String(session?.user?.email || session?.user?.login || "").trim();
    const internalSecret = await resolveInternalSecret();
    const adminBaseUrl = await resolveAdminApiBaseUrl();

    if (!userIdentifier || !internalSecret) {
        throw new Error("未找到当前用户或内部服务密钥");
    }

    return fetch(`${adminBaseUrl}${path}`, {
        ...init,
        headers: {
            "Content-Type": "application/json",
            "x-v8-agent-os-secret": internalSecret,
            "x-v8-agent-os-user-email": userIdentifier,
            ...(init.headers || {}),
        },
        cache: "no-store",
    });
}

export type SharedUserProfile = {
    id?: string;
    login?: string;
    email?: string;
    name?: string;
    image?: string;
    role?: string;
    mustChangePassword?: boolean;
};

export async function getUserProfile() {
    try {
        const response = await createAdminRequest("/auth/profile", {
            method: "GET",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            return { success: false, error: String(data.error || "资料读取失败") };
        }
        return { success: true, user: data as SharedUserProfile };
    } catch (error) {
        console.error("Failed to read profile:", error);
        return { success: false, error: "资料读取失败" };
    }
}

export async function updateUserNickname(nickname: string) {
    if (!nickname || nickname.trim().length < 2) {
        return { success: false, error: "昵称至少需要 2 个字符" };
    }

    try {
        const response = await createAdminRequest("/auth/profile", {
            method: "PATCH",
            body: JSON.stringify({ name: nickname.trim() }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            return { success: false, error: String(data.error || "昵称更新失败") };
        }
        return { success: true, user: data.user };
    } catch (error) {
        console.error("Failed to update nickname:", error);
        return { success: false, error: "昵称更新失败" };
    }
}

export async function updateUserAvatar(image: string) {
    try {
        const response = await createAdminRequest("/auth/profile", {
            method: "PATCH",
            body: JSON.stringify({ image }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            return { success: false, error: String(data.error || "头像更新失败") };
        }
        return { success: true, user: data.user };
    } catch (error) {
        console.error("Failed to update avatar:", error);
        return { success: false, error: "头像更新失败" };
    }
}
