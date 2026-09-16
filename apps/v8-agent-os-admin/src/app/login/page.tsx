import { redirect } from "next/navigation";

import { AdminLoginScreen } from "@/components/admin/AdminLoginScreen";
import { auth } from "@/lib/auth";
import { EngineIdentityError } from "@/lib/server/engine-identity";
import { hasOwner } from "@/lib/users";

export default async function AdminLoginPage() {
    const session = await auth();
    if (session?.user?.role === "ADMIN") {
        redirect("/admin");
    }

    let bootstrapMode = false;
    let ownerStateUnavailable = false;
    try {
        bootstrapMode = !await hasOwner();
    } catch (error) {
        if (!(error instanceof EngineIdentityError)) throw error;
        ownerStateUnavailable = true;
    }
    return <AdminLoginScreen bootstrapMode={bootstrapMode} ownerStateUnavailable={ownerStateUnavailable} />;
}
