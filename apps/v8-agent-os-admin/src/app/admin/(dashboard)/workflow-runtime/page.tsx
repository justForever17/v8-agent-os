import { redirect } from "next/navigation";

export default function WorkflowRuntimePage() {
    redirect("/admin/memory?tab=projects");
}
