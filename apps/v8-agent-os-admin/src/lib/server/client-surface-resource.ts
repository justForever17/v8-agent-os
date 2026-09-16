/** Engine owns resource capability signatures; Admin is a transport facade. */
import { engineIdentity } from "@/lib/server/engine-identity";

export async function buildSignedClientSurfaceUrl(path: string, options?: { ttlSeconds?: number; absolute?: boolean; publicBaseUrl?: string; sessionId?: string }) {
    const result = await engineIdentity<{ signedUrl: string }>("/resource-link", { method: "POST", body: JSON.stringify({ path, sessionId: options?.sessionId || "" }) });
    return result.signedUrl;
}
