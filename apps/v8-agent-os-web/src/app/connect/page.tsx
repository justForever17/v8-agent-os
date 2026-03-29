import { Suspense } from "react";

import { ConnectPageClient } from "@/app/connect/ConnectPageClient";
import { ConnectPageSkeleton } from "@/app/connect/ConnectPageSkeleton";

export default function ConnectPage() {
    return (
        <Suspense fallback={<ConnectPageSkeleton />}>
            <ConnectPageClient />
        </Suspense>
    );
}
