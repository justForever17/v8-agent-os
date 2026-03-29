import { redirect } from "next/navigation";

import { getActiveAdminConnection } from "@/lib/server/admin-connection";

export async function requireAdminConnection(nextPath: string) {
    const connection = await getActiveAdminConnection();
    if (!connection) {
        redirect(`/connect?next=${encodeURIComponent(nextPath)}`);
    }
    return connection;
}
