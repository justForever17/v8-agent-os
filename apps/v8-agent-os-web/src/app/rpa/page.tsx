import { RPAQuickPanel } from "@/components/rpa/RPAQuickPanel";
import { auth } from "@/lib/auth";
import { RPAAuthGate } from "@/app/rpa/RPAAuthGate";

export const dynamic = "force-dynamic";

export default async function RPAPage() {
    const session = await auth();

    if (!session?.user) {
        return <RPAAuthGate />;
    }

    return <RPAQuickPanel />;
}
