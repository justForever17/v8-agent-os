import { redirect } from "next/navigation";

export default function AgentsRedirectPage() {
    redirect("/admin/subagents");
}
