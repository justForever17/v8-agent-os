import { redirect } from "next/navigation";

export default function ContextConfigPage() {
    redirect("/admin/memory?tab=context");
}
