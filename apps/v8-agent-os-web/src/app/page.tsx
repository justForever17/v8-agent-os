import { redirect } from "next/navigation";

import { requireAdminConnection } from "@/lib/server/page-guards";

export default async function RootPage() {
    await requireAdminConnection("/chat");
    redirect("/chat");
}
