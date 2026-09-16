import { getClientProxyConfig } from "@/lib/server/runtime-config";

export async function requireLocalEngine(_nextPath: string) {
    if (!(await getClientProxyConfig()).internalSecret) throw new Error("Local Engine is not configured");
}
