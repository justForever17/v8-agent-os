import { redirect } from "next/navigation";

export default function MemoryRuntimeRedirectPage() {
    redirect("/admin/memory?tab=runtime");
}
