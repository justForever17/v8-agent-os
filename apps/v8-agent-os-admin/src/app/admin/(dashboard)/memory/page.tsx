import { redirect } from "next/navigation";

import MemoryDashboardClient from "@/components/memory/MemoryDashboardClient";

type MemoryDashboardPageProps = {
    searchParams?: Promise<Record<string, string | string[] | undefined>> | Record<string, string | string[] | undefined>;
};

function resolveTab(
    searchParams: Record<string, string | string[] | undefined> | undefined,
): string {
    const value = searchParams?.tab;
    if (Array.isArray(value)) {
        return String(value[0] || "preferences");
    }
    return String(value || "preferences");
}

export default async function MemoryDashboardPage({ searchParams }: MemoryDashboardPageProps) {
    const resolvedSearchParams = searchParams instanceof Promise ? await searchParams : searchParams;
    const requestedTab = resolveTab(resolvedSearchParams);

    if (requestedTab === "projects") {
        redirect("/admin/memory?tab=workflows");
    }

    return <MemoryDashboardClient initialRequestedTab={requestedTab} />;
}
