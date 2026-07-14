import { redirect } from "next/navigation";
import { Suspense } from "react";

import { UiPatchWorkbench } from "@/components/ui-patch/UiPatchWorkbench";
import { auth } from "@/lib/auth";
import { requireAdminConnection } from "@/lib/server/page-guards";


export const dynamic = "force-dynamic";

export default async function UiPatchPage() {
    await requireAdminConnection("/ui-patch");
    const session = await auth();
    if (!session?.user) redirect("/chat");
    return (
        <Suspense fallback={<div className="flex h-full w-full items-center justify-center text-sm text-muted-foreground">Loading UI Patch Workbench…</div>}>
            <UiPatchWorkbench />
        </Suspense>
    );
}
