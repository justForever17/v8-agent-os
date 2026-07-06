"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type TopbarGlowTone = "amber" | "blue" | "cyan" | "emerald" | "fuchsia" | "rose" | "sky" | "slate" | "violet";

const TONE_STYLES: Record<TopbarGlowTone, { glow: string; icon: string }> = {
    amber: {
        glow: "bg-gradient-to-r from-yellow-200 via-orange-200 to-pink-200",
        icon: "text-orange-500 dark:text-orange-300",
    },
    blue: {
        glow: "bg-gradient-to-r from-purple-600 via-blue-600 to-indigo-600",
        icon: "text-blue-500 dark:text-blue-300",
    },
    cyan: {
        glow: "bg-gradient-to-r from-cyan-300 via-sky-300 to-blue-400",
        icon: "text-cyan-500 dark:text-cyan-300",
    },
    emerald: {
        glow: "bg-gradient-to-r from-emerald-300 via-teal-300 to-cyan-300",
        icon: "text-emerald-600 dark:text-emerald-300",
    },
    fuchsia: {
        glow: "bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500",
        icon: "text-fuchsia-500 dark:text-fuchsia-300",
    },
    rose: {
        glow: "bg-gradient-to-r from-rose-300 via-pink-300 to-orange-300",
        icon: "text-rose-500 dark:text-rose-300",
    },
    sky: {
        glow: "bg-gradient-to-r from-sky-300 via-blue-300 to-indigo-300",
        icon: "text-sky-500 dark:text-sky-300",
    },
    slate: {
        glow: "bg-gradient-to-r from-slate-200 via-slate-300 to-zinc-300",
        icon: "text-slate-500 dark:text-slate-300",
    },
    violet: {
        glow: "bg-gradient-to-r from-violet-300 via-purple-300 to-fuchsia-300",
        icon: "text-violet-500 dark:text-violet-300",
    },
};

type TopbarGlowActionButtonProps = React.ComponentPropsWithoutRef<typeof Button> & {
    tone?: TopbarGlowTone;
};

export const TopbarGlowActionButton = React.forwardRef<HTMLDivElement, TopbarGlowActionButtonProps>(
    ({ tone = "slate", className, children, ...props }, ref) => {
        const toneStyle = TONE_STYLES[tone];

        return (
            <div ref={ref} className="group relative flex h-[25px] w-[25px] items-center justify-center">
                <div
                    className={cn(
                        "pointer-events-none absolute inset-0 rounded-full opacity-0 blur-md transition-opacity duration-500 group-hover:opacity-100",
                        "animate-gradient-xy",
                        toneStyle.glow,
                    )}
                />
                <Button
                    variant="ghost"
                    size="icon"
                    className={cn(
                        "relative h-[25px] w-[25px] overflow-hidden rounded-full border border-transparent bg-white/50 p-0 transition-all duration-500 hover:border-border/50 hover:bg-white/80",
                        "dark:bg-white/[0.06] dark:hover:bg-white/[0.1]",
                        "[&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:shrink-0",
                        toneStyle.icon,
                        className,
                    )}
                    {...props}
                >
                    {children}
                </Button>
            </div>
        );
    },
);
TopbarGlowActionButton.displayName = "TopbarGlowActionButton";
