import { resolveInternalSecret } from "@/lib/server/runtime-config";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
import { timingSafeEqual } from "node:crypto";
import { engineIdentity } from "@/lib/server/engine-identity";
export async function verifyServiceAuth(req: Request): Promise<string | null> {
  const secret = req.headers.get("x-v8-agent-os-secret");
  const internalSecret = resolveInternalSecret();
  if (!internalSecret) {
    console.warn(INTERNAL_READABLE.k4b0c4c45f3);
    return null;
  }
  if (!secret || Buffer.byteLength(secret) !== Buffer.byteLength(internalSecret) || !timingSafeEqual(Buffer.from(secret), Buffer.from(internalSecret))) {
    return null; // Invalid secret
  }
  const { user } = await engineIdentity<{ user?: { sessionIdentifier?: string; email?: string; login?: string } }>("/owner");
  return user?.sessionIdentifier || user?.email || user?.login || null;
}
