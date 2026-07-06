"use client";

import { useVoiceStore } from "@/store/useVoiceStore";
import { Volume2, VolumeX } from "lucide-react";
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useT } from "@/components/providers/LocaleProvider";

export function VoiceToggle() {
    const { isVoiceEnabled, toggleVoice } = useVoiceStore();
    const t = useT();

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <TopbarGlowActionButton
                        onClick={toggleVoice}
                        tone={isVoiceEnabled ? "cyan" : "slate"}
                        aria-label={t("web.generated.3acce04edd")}
                        title={t("web.generated.3acce04edd")}
                    >
                        {isVoiceEnabled ? (
                           <Volume2 />
                        ) : (
                           <VolumeX />
                        )}
                    </TopbarGlowActionButton>
                </TooltipTrigger>
                <TooltipContent>
                    <p>{isVoiceEnabled ? t("web.generated.0dab312994") : t("web.generated.5478aac706")}</p>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
