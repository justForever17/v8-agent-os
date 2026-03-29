import { cn } from "@/lib/utils";

export function AdminPageShell({
    children,
    className,
}: {
    children: React.ReactNode;
    className?: string;
}) {
    return <div className={cn("mx-auto flex w-full max-w-7xl flex-col gap-6", className)}>{children}</div>;
}
