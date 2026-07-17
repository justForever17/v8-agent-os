"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Square, RotateCcw, Volume2, Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { useVoiceStore } from "@/store/useVoiceStore";
import { cn } from "@/lib/utils";

interface VoiceCardProps {
    content: string;
    isStreaming?: boolean;
}

export function VoiceCard({ content, isStreaming }: VoiceCardProps) {
    const t = useT();
    const { isVoiceEnabled, setSpeaking } = useVoiceStore();
    const [isPlaying, setIsPlaying] = useState(false);
    const [isGenerating, setIsGenerating] = useState(false);
    const [audioUrl, setAudioUrl] = useState<string | null>(null);
    const [showText, setShowText] = useState(false);
    const [hasPlayed, setHasPlayed] = useState(false);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const hasRequestedAudio = useRef(false);
    const waveBars = useMemo(
        () =>
            Array.from({ length: 8 }, (_, index) => ({
                key: index,
                height: `${28 + ((index * 17) % 61)}%`,
                duration: `${0.38 + (index % 5) * 0.08}s`,
            })),
        [],
    );

    // Effect: Request Audio when component mounts and TTS is enabled
    useEffect(() => {
        // Do not request if still streaming text (wait for full sentence/block) or disabled, or already requested
        if (isStreaming || !isVoiceEnabled || hasRequestedAudio.current || !content.trim()) return;
        
        hasRequestedAudio.current = true;
        generateAndPlayAudio(content);
        
        return () => {
            if (audioUrl) URL.revokeObjectURL(audioUrl);
            setSpeaking(false);
        };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming, isVoiceEnabled, content]);

    const generateAndPlayAudio = async (text: string) => {
        try {
            setIsGenerating(true);
            setSpeaking(true);
            
            const response = await fetch("/api/audio/tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            });

            if (!response.ok) throw new Error("TTS generation failed");

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            setAudioUrl(url);
            
            // Automatically play if voice is globally enabled
            if (isVoiceEnabled && audioRef.current) {
                audioRef.current.src = url;
                audioRef.current.play().catch(e => console.error("Auto-play prevented:", e));
                setIsPlaying(true);
            }
        } catch (error) {
            console.error("Voice Generation Error:", error);
            // Fallback: show text if audio fails
            setShowText(true);
        } finally {
            setIsGenerating(false);
        }
    };

    const togglePlayback = () => {
        if (!audioRef.current || !audioUrl) {
            // First time manual trigger
            if (!hasRequestedAudio.current) {
                 hasRequestedAudio.current = true;
                 generateAndPlayAudio(content);
            }
            return;
        }

        if (isPlaying) {
            audioRef.current.pause();
            setIsPlaying(false);
            setSpeaking(false);
        } else {
            audioRef.current.play().catch(console.error);
            setIsPlaying(true);
            setSpeaking(true);
        }
    };

    const handleEnded = () => {
        setIsPlaying(false);
        setSpeaking(false);
        setHasPlayed(true);
        setShowText(true); // Auto reveal text after hearing it once
    };

    return (
        <div className="flex flex-col gap-2 my-2 w-full max-w-sm">
            {/* The Audio Player UI */}
            <div className={cn(
                "flex items-center gap-3 p-3 rounded-2xl border transition-all duration-300",
                isPlaying 
                    ? "bg-gradient-to-r from-blue-500/10 to-transparent border-blue-500/30" 
                    : "bg-background/50 border-white/10"
            )}>
                {/* Play/Pause Button */}
                <Button 
                    variant="default" 
                    size="icon" 
                    className={cn("h-10 w-10 rounded-full shrink-0 shadow-lg", isPlaying ? "bg-blue-600 hover:bg-blue-700" : "")}
                    onClick={togglePlayback}
                    disabled={isGenerating || isStreaming}
                >
                    {isGenerating || isStreaming ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : isPlaying ? (
                        <Square className="h-4 w-4 fill-current" />
                    ) : hasPlayed ? (
                        <RotateCcw className="h-4 w-4" />
                    ) : (
                        <Play className="h-4 w-4 fill-current ml-0.5" />
                    )}
                </Button>

                {/* Animated Soundwave or Status */}
                <div className="flex-1 flex items-center justify-between overflow-hidden">
                    <div className="flex items-center gap-1.5 h-6">
                        {isPlaying ? (
                            waveBars.map((bar) => (
                                <div 
                                    key={bar.key}
                                    className="w-1 bg-blue-500 rounded-full animate-pulse"
                                    style={{ 
                                        height: bar.height,
                                        animationDuration: bar.duration,
                                    }}
                                />
                            ))
                        ) : (
                            <div className="flex items-center gap-2 text-sm text-muted-foreground font-medium">
                                <Volume2 className="h-4 w-4" />
                                {isGenerating ? t('web.voice.generating') :
                                 isStreaming ? t('web.voice.receiving') :
                                 hasPlayed ? t('web.voice.clip') : t('web.voice.play')}
                            </div>
                        )}
                    </div>
                </div>

                {/* Text Toggle Button */}
                <Button 
                    variant="ghost" 
                    size="icon" 
                    className="h-8 w-8 text-muted-foreground hover:text-foreground shrink-0"
                    onClick={() => setShowText(!showText)}
                >
                    {showText ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </Button>
            </div>

            {/* Hidden Text Reveal */}
            <div className={cn(
                "overflow-hidden transition-all duration-300 ease-in-out",
                showText ? "max-h-96 opacity-100 mt-1" : "max-h-0 opacity-0"
            )}>
                <div className="p-3 bg-black/20 rounded-xl text-sm border border-white/5 leading-relaxed">
                    <span className="text-muted-foreground mr-1 select-none">“</span>
                    {content}
                    <span className="text-muted-foreground ml-1 select-none">”</span>
                </div>
            </div>

            {/* Hidden Audio Element */}
            {audioUrl && (
                 <audio 
                    ref={audioRef} 
                    src={audioUrl} 
                    onEnded={handleEnded}
                    onPause={() => { setIsPlaying(false); setSpeaking(false); }}
                    onPlay={() => { setIsPlaying(true); setSpeaking(true); }}
                 />
            )}
        </div>
    );
}
