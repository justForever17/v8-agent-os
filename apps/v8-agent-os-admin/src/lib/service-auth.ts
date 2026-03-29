import { resolveInternalSecret } from "@/lib/server/runtime-config";

export async function verifyServiceAuth(req: Request): Promise<string | null> {
    const secret = req.headers.get("x-v8-agent-os-secret");
    const userEmail = req.headers.get("x-v8-agent-os-user-email");

    const internalSecret = resolveInternalSecret();

    if (!internalSecret) {
        console.warn("[Auth] 未找到内部服务密钥，拒绝服务鉴权。");
        return null;
    }

    if (secret !== internalSecret) {
        return null; // Invalid secret
    }

    if (!userEmail) {
        return null; // Missing user context
    }

    return userEmail;
}

