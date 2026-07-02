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
import { lt } from "@/lib/locale";
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
        return <div className="h-9 w-9 animate-pulse bg-muted rounded-full" />;
    }

    if (!session?.user) {
        return (
            <Button
                variant="ghost"
                className="h-9 w-9 rounded-full overflow-hidden border border-transparent bg-white/50 p-0 dark:bg-slate-950/50"
                title={t(lt("本机连接中", "Connecting locally"))}
            >
                <Avatar className="h-8 w-8">
                    <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                    <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-sm font-medium text-white">
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
                                "relative h-9 w-9 rounded-full overflow-hidden transition-all duration-500 border border-transparent hover:border-border/50",
                                "bg-white/50 dark:bg-slate-950/50 hover:bg-white/80 dark:hover:bg-slate-900/80"
                            )}
                        >
                            <Avatar className="h-8 w-8">
                                <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                                <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-white text-sm font-medium">
                                    {displayName.charAt(0).toUpperCase() || "U"}
                                </AvatarFallback>
                            </Avatar>
                        </Button>
                    </div>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56" align="end" forceMount>
                    <DropdownMenuLabel className="font-normal">
                        <div className="flex flex-col space-y-1">
                            <p className="text-sm font-medium leading-none">{displayName || t(lt("聊天用户", "Chat user"))}</p>
                            <p className="text-xs leading-none text-muted-foreground">
                                {displayLogin}
                            </p>
                        </div>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setShowSettings(true)}>
                        <Settings className="mr-2 h-4 w-4" />
                        <span>{t(lt("个性化设置", "Personalization"))}</span>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <SettingsDialog open={showSettings} onOpenChange={setShowSettings} />
        </>
    );
}
