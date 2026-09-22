"use client";
import { Suspense } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useT } from "@admin/components/providers/LocaleProvider";
const SupervisorPage = dynamic(() => import("../supervisor/page"));
const SubagentsPage = dynamic(() => import("../subagents/page"));
function ChatRuntimeInner() {
    const t = useT();
    const tab = useSearchParams().get("tab") === "subagents" ? "subagents" : "supervisor";
    return <div className="space-y-4">
        <nav className="admin-route-tabs" aria-label={t("admin.experience.agents")}>
            <Link href="/admin/chat-runtime?tab=supervisor" scroll={false} aria-current={tab === "supervisor" ? "page" : undefined}>{t("app.admin.dashboard.chat.runtime.page.kbae659f3")}</Link>
            <Link href="/admin/chat-runtime?tab=subagents" scroll={false} aria-current={tab === "subagents" ? "page" : undefined}>{t("app.admin.dashboard.chat.runtime.page.k0354845a")}</Link>
        </nav>
        {tab === "supervisor" ? <SupervisorPage/> : <SubagentsPage/>}
    </div>;
}
export default function ChatRuntimePage() { return <Suspense><ChatRuntimeInner/></Suspense>; }
