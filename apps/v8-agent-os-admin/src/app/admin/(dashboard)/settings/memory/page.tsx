import { redirect } from "next/navigation";

export default function MemorySettingsPage() {
    redirect("/admin/memory?tab=config");
}
