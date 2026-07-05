import { redirect } from "next/navigation";

import { getActiveAdminConnection } from "@/lib/server/admin-connection";

export async function requireAdminConnection(_nextPath: string) {
    const connection = await getActiveAdminConnection();
    if (!connection) {
        redirect("/chat");
    }
    return connection;
}
