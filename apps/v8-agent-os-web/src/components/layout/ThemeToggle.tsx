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
            <Button variant="ghost" size="icon" className="w-9 h-9 rounded-full">
                <span className="sr-only">Toggle theme</span>
            </Button>
        );
    }

    const isDark = theme === "dark";

    return (
        <div className="relative group translate-y-0.5">
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
                    "relative w-9 h-9 rounded-full overflow-hidden transition-all duration-500 border border-transparent hover:border-border/50",
                    isDark
                        ? "bg-slate-950/50 hover:bg-slate-900/80 text-blue-400"
                        : "bg-white/50 hover:bg-white/80 text-orange-500"
                )}
                onClick={() => setTheme(isDark ? "light" : "dark")}
            >
                <div className="relative w-full h-full flex items-center justify-center">
                    <Sun className={cn(
                        "h-[1.2rem] w-[1.2rem] absolute transition-all duration-500 rotate-0 scale-100",
                        isDark ? "-rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
                    )} />
                    <Moon className={cn(
                        "h-[1.2rem] w-[1.2rem] absolute transition-all duration-500 rotate-90 scale-0",
                        isDark ? "rotate-0 scale-100 opacity-100" : "rotate-90 scale-0 opacity-0"
                    )} />
                </div>
                <span className="sr-only">Toggle theme</span>
            </Button>
        </div>
    );
}
