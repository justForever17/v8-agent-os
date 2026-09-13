"use client";

import { Volume2, VolumeX, Play, Pause, SkipForward } from "lucide-react";

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

export function BackgroundPlaybackControls() {
    const t = useT();
    const { enabled, paused, togglePaused, multiple, next, error } = useBackgroundVideoAudio();
    if (!enabled && !error) return null;
    return <div className="flex items-center gap-1">
        <TopbarGlowActionButton onClick={togglePaused} aria-label={t(paused ? "web.background.play" : "web.background.pause")} title={t(error || (paused ? "web.background.play" : "web.background.pause"))} tone="slate">
            {paused ? <Play /> : <Pause />}
        </TopbarGlowActionButton>
        {multiple ? <TopbarGlowActionButton onClick={next} aria-label={t("web.background.next")} title={t("web.background.next")} tone="slate"><SkipForward /></TopbarGlowActionButton> : null}
        {error ? <span role="status" className="max-w-40 truncate text-xs text-destructive" title={t(error)}>{t(error)}</span> : null}
    </div>;
}
