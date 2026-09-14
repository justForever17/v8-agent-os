"use client";
import { Suspense } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useT } from "@/components/providers/LocaleProvider";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { AutomationDeliveryReview } from "@/components/automation/AutomationDeliveryReview";
const HooksPage = dynamic(() => import("./hooks/page"));
const ScheduledTasksPage = dynamic(() => import("./cron/page"));
const WakeIngressPolicyCard = dynamic(() => import("@/components/automation/WakeIngressPolicyCard").then(module => module.WakeIngressPolicyCard));
function AutomationOverviewInner() {
    const t = useT();
    const tab = useSearchParams().get("tab") === "hooks" ? "hooks" : "cron";
    return <div className="space-y-4">
        <nav className="admin-route-tabs" aria-label={t("admin.experience.tasks")}>
            <Link href="/admin/automation?tab=cron" scroll={false} aria-current={tab === "cron" ? "page" : undefined}>{t("app.admin.dashboard.automation.page.k8164146c")}</Link>
            <Link href="/admin/automation?tab=hooks" scroll={false} aria-current={tab === "hooks" ? "page" : undefined}>{t("app.admin.dashboard.automation.page.ka206d935")}</Link>
        </nav>
        <AutomationDeliveryReview/>
        {tab === "hooks" ? <div className="space-y-4"><HooksPage/><AdvancedSection title="admin.experience.policies"><WakeIngressPolicyCard/></AdvancedSection></div> : <ScheduledTasksPage/>}
    </div>;
}
export default function AutomationOverviewPage() { return <Suspense><AutomationOverviewInner/></Suspense>; }
