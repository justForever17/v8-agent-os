"use client";

import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Music, Play, Pause, SkipForward, SkipBack, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

interface MusicTrack {
    id: string;
    title: string;
    url: string;
}

interface MusicPlayerProps {
    isCollapsed?: boolean;
}

export function MusicPlayer({ isCollapsed = false }: MusicPlayerProps) {
    const [tracks, setTracks] = useState<MusicTrack[]>([]);
    const [currentTrackIndex, setCurrentTrackIndex] = useState(0);
    const [isPlaying, setIsPlaying] = useState(false);
    const [volume, setVolume] = useState([30]);
    const [isMuted, setIsMuted] = useState(false);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    useEffect(() => {
        fetch('/api/music')
            .then(res => res.json())
            .then(data => {
                if (Array.isArray(data) && data.length > 0) {
                    setTracks(data);
                } else {
                    // Fallback default track
                    setTracks([{
                        id: 'default',
                        title: 'Chill Lo-Fi Beats',
                        url: 'https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3'
                    }]);
                }
            })
            .catch(err => console.error("Failed to load music", err));
    }, []);

    useEffect(() => {
        if (audioRef.current) {
            audioRef.current.volume = isMuted ? 0 : volume[0] / 100;
        }
    }, [volume, isMuted]);

    useEffect(() => {
        if (audioRef.current) {
            if (isPlaying) {
                audioRef.current.play().catch(() => setIsPlaying(false));
            } else {
                audioRef.current.pause();
            }
        }
    }, [isPlaying, currentTrackIndex]);

    const handleNext = () => {
        setCurrentTrackIndex((prev) => (prev + 1) % tracks.length);
        setIsPlaying(true);
    };

    const handlePrev = () => {
        setCurrentTrackIndex((prev) => (prev - 1 + tracks.length) % tracks.length);
        setIsPlaying(true);
    };

    const toggleMute = () => setIsMuted(!isMuted);

    const currentTrack = tracks[currentTrackIndex];

    if (!currentTrack) return null;

    return (
        <div className={cn("transition-all duration-300", isCollapsed ? "p-2" : "p-4")}>
            <audio
                ref={audioRef}
                src={currentTrack.url}
                onEnded={handleNext}
                loop={tracks.length === 1}
            />

            {/* Widget Container */}
            <div className={cn(
                "relative overflow-hidden transition-all duration-300 bg-background/50 backdrop-blur-md border border-white/10 dark:border-white/5 shadow-sm",
                isCollapsed ? "rounded-full h-10 w-10 flex items-center justify-center cursor-pointer hover:bg-accent" : "rounded-2xl p-3"
            )}
                onClick={isCollapsed ? () => setIsPlaying(!isPlaying) : undefined}
            >
                {/* Collapsed View */}
                {isCollapsed ? (
                    <div className={cn("relative flex items-center justify-center w-full h-full text-primary")}>
                        {isPlaying ? (
                            <div className="flex gap-0.5 items-center h-3">
                                <span className="w-0.5 h-full bg-current animate-[music-bar_0.5s_ease-in-out_infinite]" />
                                <span className="w-0.5 h-full bg-current animate-[music-bar_0.6s_ease-in-out_infinite_0.1s]" />
                                <span className="w-0.5 h-full bg-current animate-[music-bar_0.5s_ease-in-out_infinite_0.2s]" />
                            </div>
                        ) : (
                            <Music className="w-4 h-4" />
                        )}
                    </div>
                ) : (
                    // Expanded View
                    <>
                        {/* Album Art / Visualizer Placeholder */}
                        <div className="flex items-center gap-3 mb-3">
                            <div className={cn(
                                "relative w-10 h-10 rounded-xl overflow-hidden flex items-center justify-center shrink-0 bg-primary/10 border border-primary/20",
                                isPlaying ? "shadow-lg shadow-primary/20" : ""
                            )}>
                                {isPlaying ? (
                                    <div className="absolute inset-0 flex items-center justify-center gap-0.5 bg-gradient-to-br from-primary/20 to-violet-500/20">
                                        <span className="w-1 h-4 bg-primary rounded-full animate-[music-bar_0.5s_ease-in-out_infinite]" />
                                        <span className="w-1 h-6 bg-primary rounded-full animate-[music-bar_0.7s_ease-in-out_infinite_0.1s]" />
                                        <span className="w-1 h-3 bg-primary rounded-full animate-[music-bar_0.5s_ease-in-out_infinite_0.2s]" />
                                    </div>
                                ) : (
                                    <Music className="w-5 h-5 text-primary" />
                                )}
                            </div>

                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-semibold truncate text-foreground">{currentTrack.title}</p>
                                <p className="text-[10px] text-muted-foreground truncate uppercase tracking-wider">Now Playing</p>
                            </div>

                            <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" onClick={toggleMute}>
                                {isMuted || volume[0] === 0 ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
                            </Button>
                        </div>

                        {/* Controls */}
                        <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-1">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7 rounded-full hover:bg-accent"
                                    onClick={handlePrev}
                                    disabled={tracks.length <= 1}
                                >
                                    <SkipBack className="w-3.5 h-3.5" />
                                </Button>
                                <Button
                                    size="icon"
                                    className="h-8 w-8 rounded-full shadow-md bg-primary text-primary-foreground hover:bg-primary/90"
                                    onClick={() => setIsPlaying(!isPlaying)}
                                >
                                    {isPlaying ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current ml-0.5" />}
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7 rounded-full hover:bg-accent"
                                    onClick={handleNext}
                                    disabled={tracks.length <= 1}
                                >
                                    <SkipForward className="w-3.5 h-3.5" />
                                </Button>
                            </div>

                            <Slider
                                value={isMuted ? [0] : volume}
                                onValueChange={(val) => {
                                    setVolume(val);
                                    setIsMuted(val[0] === 0);
                                }}
                                max={100}
                                step={1}
                                className="w-16"
                            />
                        </div>
                    </>
                )}
            </div>
            <style jsx global>{`
                @keyframes music-bar {
                    0%, 100% { height: 40%; opacity: 0.5; }
                    50% { height: 90%; opacity: 1; }
                }
            `}</style>
        </div>
    );
}
