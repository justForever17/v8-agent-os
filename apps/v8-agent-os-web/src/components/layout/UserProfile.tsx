"use client";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Settings } from "lucide-react";
import { useSession } from "next-auth/react";
import { useState } from "react";
import { SettingsDialog } from "@/components/settings/SettingsDialog";
import { useT } from "@/components/providers/LocaleProvider";
import { resolveProfileAvatarSrc, useClientProfile } from "@/hooks/use-client-profile";

export function UserProfile() {
    const { data: session, status } = useSession();
    const { profile } = useClientProfile();
    const [showSettings, setShowSettings] = useState(false);
    const t = useT();
    const displayName = profile?.name || session?.user?.name || "";
    const displayImage = profile?.image || session?.user?.image || "";
    const displayLogin = profile?.login || session?.user?.login || session?.user?.email || "";

    if (status === "loading") {
        return <div className="h-[25px] w-[25px] animate-pulse rounded-full bg-muted" />;
    }

    if (!session?.user) {
        return (
            <Button
                variant="ghost"
                className="h-[25px] w-[25px] overflow-hidden rounded-full border border-transparent bg-white/50 p-0 dark:bg-slate-950/50"
                title={t("web.generated.b7c2e5097e")}
            >
                <Avatar className="h-5 w-5">
                    <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                    <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-[10px] font-medium text-white">
                        {displayName.charAt(0).toUpperCase() || "U"}
                    </AvatarFallback>
                </Avatar>
            </Button>
        );
    }

    return (
        <>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <div className="relative group">
                        {/* Flowing Gradient Background */}
                        <div
                            className={cn(
                                "absolute inset-0 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-500 blur-md",
                                "bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 animate-gradient-xy"
                            )}
                        />
                        <Button
                            variant="ghost"
                            className={cn(
                                "relative h-[25px] w-[25px] overflow-hidden rounded-full border border-transparent transition-all duration-500 hover:border-border/50",
                                "bg-white/50 dark:bg-slate-950/50 hover:bg-white/80 dark:hover:bg-slate-900/80"
                            )}
                        >
                            <Avatar className="h-5 w-5">
                                <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                                <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-[10px] font-medium text-white">
                                    {displayName.charAt(0).toUpperCase() || "U"}
                                </AvatarFallback>
                            </Avatar>
                        </Button>
                    </div>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56" align="end" forceMount>
                    <DropdownMenuLabel className="font-normal">
                        <div className="flex flex-col space-y-1">
                            <p className="text-sm font-medium leading-none">{displayName || t("web.generated.d2ccadbbf7")}</p>
                            <p className="text-xs leading-none text-muted-foreground">
                                {displayLogin}
                            </p>
                        </div>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setShowSettings(true)}>
                        <Settings className="mr-2 h-4 w-4" />
                        <span>{t("web.generated.ee712854da")}</span>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <SettingsDialog open={showSettings} onOpenChange={setShowSettings} />
        </>
    );
}
