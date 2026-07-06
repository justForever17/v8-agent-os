"use client";

import { useVoiceStore } from "@/store/useVoiceStore";
import { Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useT } from "@/components/providers/LocaleProvider";

export function VoiceToggle() {
    const { isVoiceEnabled, isSpeaking, toggleVoice } = useVoiceStore();
    const t = useT();

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={toggleVoice}
                        className={`relative h-[25px] w-[25px] rounded-full p-0 transition-all duration-300 ${
                            isVoiceEnabled 
                                ? isSpeaking 
                                    ? "bg-gradient-to-tr from-cyan-400/20 to-blue-500/20 text-cyan-500 hover:text-cyan-600 dark:text-cyan-400"
                                    : "text-foreground"
                                : "text-muted-foreground"
                        }`}
                        aria-label={t("web.generated.3acce04edd")}
                    >
                        {isVoiceEnabled ? (
                           <Volume2 className="h-3.5 w-3.5" />
                        ) : (
                           <VolumeX className="h-3.5 w-3.5" />
                        )}
                        
                        {/* Soundwave Animation */}
                        {isVoiceEnabled && isSpeaking && (
                            <span className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                <span className="absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-20 animate-ping"></span>
                                <span className="absolute inline-flex h-[120%] w-[120%] rounded-full bg-blue-400 opacity-10 animate-ping" style={{ animationDelay: '0.2s' }}></span>
                            </span>
                        )}
                    </Button>
                </TooltipTrigger>
                <TooltipContent>
                    <p>{isVoiceEnabled ? t("web.generated.0dab312994") : t("web.generated.5478aac706")}</p>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
