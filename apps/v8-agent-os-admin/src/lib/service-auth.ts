import { resolveInternalSecret } from "@/lib/server/runtime-config";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
export async function verifyServiceAuth(req: Request): Promise<string | null> {
  const secret = req.headers.get("x-v8-agent-os-secret");
  const userEmail = req.headers.get("x-v8-agent-os-user-email");
  const internalSecret = resolveInternalSecret();
  if (!internalSecret) {
    console.warn(INTERNAL_READABLE.k4b0c4c45f3);
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
