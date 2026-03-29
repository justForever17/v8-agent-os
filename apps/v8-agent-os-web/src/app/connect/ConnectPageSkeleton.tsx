export function ConnectPageSkeleton() {
    return (
        <div className="flex min-h-0 w-full flex-1 items-center justify-center overflow-y-auto bg-[radial-gradient(circle_at_top,_rgba(124,58,237,0.10),_transparent_42%),linear-gradient(180deg,_#f8fafc_0%,_#f1f5f9_100%)] px-4 py-6 dark:bg-[radial-gradient(circle_at_top,_rgba(124,58,237,0.16),_transparent_38%),linear-gradient(180deg,_#020617_0%,_#0f172a_100%)] sm:px-6 sm:py-10">
            <div className="mx-auto w-full max-w-lg rounded-[2rem] border border-slate-200/80 bg-white/95 p-5 shadow-[0_24px_80px_-36px_rgba(15,23,42,0.35)] dark:border-slate-800/80 dark:bg-slate-950/92 sm:p-7">
                <div className="space-y-4">
                    <div className="h-4 w-16 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" />
                    <div className="h-6 w-32 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" />
                    <div className="space-y-2">
                        <div className="h-4 w-full animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800/80" />
                        <div className="h-4 w-2/3 animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800/80" />
                    </div>
                    <div className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800/80" />
                </div>
            </div>
        </div>
    );
}
