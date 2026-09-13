import { cn } from "@/lib/utils";

export function AdminPageShell({
    children,
    className,
}: {
    children: React.ReactNode;
    className?: string;
}) {
    return <div className={cn("admin-page mx-auto flex w-full max-w-[1040px] flex-col gap-5", className)}>{children}</div>;
}
