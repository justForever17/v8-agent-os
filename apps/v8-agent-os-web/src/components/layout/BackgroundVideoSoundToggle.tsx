"use client";

import { Volume2, VolumeX } from "lucide-react";

import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";
import { useT } from "@/components/providers/LocaleProvider";
import { useBackgroundVideoAudio } from "@/components/providers/PersonalizationProvider";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function BackgroundVideoSoundToggle() {
    const { available, muted, toggleMuted } = useBackgroundVideoAudio();
    const t = useT();

    if (!available) return null;

    const label = muted
        ? t("web.personalization.background.unmuteVideo")
        : t("web.personalization.background.muteVideo");

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <TopbarGlowActionButton
                        onClick={toggleMuted}
                        tone={muted ? "slate" : "cyan"}
                        aria-label={label}
                        title={label}
                    >
                        {muted ? <VolumeX /> : <Volume2 />}
                    </TopbarGlowActionButton>
                </TooltipTrigger>
                <TooltipContent><p>{label}</p></TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
