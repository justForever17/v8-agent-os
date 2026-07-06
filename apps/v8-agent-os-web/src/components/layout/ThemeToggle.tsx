"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ThemeToggle() {
    const { theme, setTheme } = useTheme();
    const [mounted, setMounted] = React.useState(false);

    React.useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) {
        return (
            <Button variant="ghost" size="icon" className="h-[25px] w-[25px] rounded-full">
                <span className="sr-only">Toggle theme</span>
            </Button>
        );
    }

    const isDark = theme === "dark";

    return (
        <div className="group relative">
            {/* Flowing Gradient Background */}
            <div
                className={cn(
                    "absolute inset-0 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-500 blur-md",
                    isDark
                        ? "bg-gradient-to-r from-purple-600 via-blue-600 to-indigo-600 animate-gradient-xy"
                        : "bg-gradient-to-r from-yellow-200 via-orange-200 to-pink-200 animate-gradient-xy"
                )}
            />

            <Button
                variant="ghost"
                size="icon"
                className={cn(
                    "relative h-[25px] w-[25px] overflow-hidden rounded-full border border-transparent transition-all duration-500 hover:border-border/50",
                    isDark
                        ? "bg-slate-950/50 hover:bg-slate-900/80 text-blue-400"
                        : "bg-white/50 hover:bg-white/80 text-orange-500"
                )}
                onClick={() => setTheme(isDark ? "light" : "dark")}
            >
                <div className="relative flex h-full w-full items-center justify-center">
                    <Sun className={cn(
                        "absolute h-3.5 w-3.5 rotate-0 scale-100 transition-all duration-500",
                        isDark ? "-rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
                    )} />
                    <Moon className={cn(
                        "absolute h-3.5 w-3.5 rotate-90 scale-0 transition-all duration-500",
                        isDark ? "rotate-0 scale-100 opacity-100" : "rotate-90 scale-0 opacity-0"
                    )} />
                </div>
                <span className="sr-only">Toggle theme</span>
            </Button>
        </div>
    );
}
