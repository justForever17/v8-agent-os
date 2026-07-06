"use client";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";
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
            <TopbarGlowActionButton
                tone="fuchsia"
                className="bg-transparent hover:bg-transparent dark:bg-transparent dark:hover:bg-transparent"
                title={t("web.generated.b7c2e5097e")}
            >
                <Avatar className="h-full w-full">
                    <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                    <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-[10px] font-medium text-white">
                        {displayName.charAt(0).toUpperCase() || "U"}
                    </AvatarFallback>
                </Avatar>
            </TopbarGlowActionButton>
        );
    }

    return (
        <>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <TopbarGlowActionButton
                        tone="fuchsia"
                        className={cn(
                            "bg-transparent hover:bg-transparent dark:bg-transparent dark:hover:bg-transparent",
                        )}
                        >
                            <Avatar className="h-full w-full">
                                <AvatarImage src={resolveProfileAvatarSrc(displayImage)} alt={displayName} />
                                <AvatarFallback className="bg-gradient-to-br from-purple-500 to-pink-500 text-[10px] font-medium text-white">
                                    {displayName.charAt(0).toUpperCase() || "U"}
                                </AvatarFallback>
                            </Avatar>
                    </TopbarGlowActionButton>
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
