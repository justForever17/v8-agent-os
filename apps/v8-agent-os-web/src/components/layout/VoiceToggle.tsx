"use client";

import { useVoiceStore } from "@/store/useVoiceStore";
import { Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

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
                        className={`relative rounded-full transition-all duration-300 ${
                            isVoiceEnabled 
                                ? isSpeaking 
                                    ? "bg-gradient-to-tr from-cyan-400/20 to-blue-500/20 text-cyan-500 hover:text-cyan-600 dark:text-cyan-400"
                                    : "text-foreground"
                                : "text-muted-foreground"
                        }`}
                        aria-label={t(lt("切换语音播报", "Toggle voice playback"))}
                    >
                        {isVoiceEnabled ? (
                           <Volume2 className="h-5 w-5" />
                        ) : (
                           <VolumeX className="h-5 w-5" />
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
                    <p>{isVoiceEnabled ? t(lt("TTS 语音播报：已开启", "TTS playback: on")) : t(lt("TTS 语音播报：已关闭", "TTS playback: off"))}</p>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
