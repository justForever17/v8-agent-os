import { cookies } from "next/headers";

import {
    AdminConnection,
    deriveAdminApiBaseUrl,
    normalizeAdminBaseUrl,
    parseAdminConnection,
    serializeAdminConnection,
} from "@/lib/admin-connection-utils";
import { ADMIN_CONNECTION_COOKIE } from "@/lib/server/runtime-config";

export async function getActiveAdminConnection() {
    const cookieStore = await cookies();
    const cookieValue = cookieStore.get(ADMIN_CONNECTION_COOKIE)?.value;
    const current = parseAdminConnection(cookieValue);
    if (current) {
        return current;
    }
    return null;
}

export { ADMIN_CONNECTION_COOKIE };
export type { AdminConnection };
export {
    deriveAdminApiBaseUrl,
    normalizeAdminBaseUrl,
    parseAdminConnection,
    serializeAdminConnection,
};
