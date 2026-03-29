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
import { LogOut, Settings } from "lucide-react";
import { signOut, useSession } from "next-auth/react";
import { useState } from "react";
import { SettingsDialog } from "@/components/settings/SettingsDialog";
import { LoginDialog } from "@/components/auth/LoginDialog";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function UserProfile() {
    const { data: session, status } = useSession();
    const [showSettings, setShowSettings] = useState(false);
    const t = useT();

    const handleSignOut = async () => {
        const fallbackUrl =
            typeof window !== "undefined"
                ? `${window.location.origin}/chat`
                : "/chat";

        const result = await signOut({
            redirect: false,
            callbackUrl: fallbackUrl,
        });

        if (typeof window === "undefined") {
            return;
        }

        let nextUrl = fallbackUrl;
        const candidate = typeof result?.url === "string" ? result.url : "";
        if (candidate) {
            try {
                const parsed = new URL(candidate, fallbackUrl);
                if (parsed.origin === window.location.origin) {
                    nextUrl = parsed.toString();
                }
            } catch {
                nextUrl = fallbackUrl;
            }
        }

        window.location.assign(nextUrl);
    };

    if (status === "loading") {
        return <div className="h-9 w-9 animate-pulse bg-muted rounded-full" />;
    }

    if (!session?.user) {
        return <LoginDialog />;
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
                                <AvatarImage src={session.user.image || ""} alt={session.user.name || ""} />
                                <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-white text-sm font-medium">
                                    {session.user.name?.charAt(0).toUpperCase() || "U"}
                                </AvatarFallback>
                            </Avatar>
                        </Button>
                    </div>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56" align="end" forceMount>
                    <DropdownMenuLabel className="font-normal">
                        <div className="flex flex-col space-y-1">
                            <p className="text-sm font-medium leading-none">{session.user.name}</p>
                            <p className="text-xs leading-none text-muted-foreground">
                                {session.user.login || session.user.email}
                            </p>
                        </div>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setShowSettings(true)}>
                        <Settings className="mr-2 h-4 w-4" />
                        <span>{t(lt("设置", "Settings"))}</span>
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={handleSignOut}>
                        <LogOut className="mr-2 h-4 w-4" />
                        <span>{t(lt("退出登录", "Sign out"))}</span>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <SettingsDialog open={showSettings} onOpenChange={setShowSettings} />
        </>
    );
}
