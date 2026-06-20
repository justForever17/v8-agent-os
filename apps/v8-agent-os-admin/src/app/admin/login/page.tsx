import { redirect } from "next/navigation";

import { AdminLoginScreen } from "@/components/admin/AdminLoginScreen";
import { auth } from "@/lib/auth";
import { hasOwner } from "@/lib/users";

export default async function AdminLoginPage() {
    const session = await auth();
    if (session?.user?.role === "ADMIN") {
        redirect("/admin");
    }

    return <AdminLoginScreen bootstrapMode={!hasOwner()} />;
}
