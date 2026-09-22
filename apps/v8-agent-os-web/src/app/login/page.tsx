import { redirect } from "next/navigation";

import { AdminLoginScreen } from "@admin/components/admin/AdminLoginScreen";
import { auth } from "@admin/lib/auth";
import { EngineIdentityError } from "@admin/lib/server/engine-identity";
import { hasOwner } from "@admin/lib/users";

export const metadata = { title: "V8 Agent OS" };

export default async function AdminLoginPage() {
    const session = await auth();
    if (session?.user?.adminAuthenticated === true && session.user.role === "ADMIN") {
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
    return (
            <AdminLoginScreen bootstrapMode={bootstrapMode} ownerStateUnavailable={ownerStateUnavailable} />
    );
}
