import React from 'react';
import { Play, Pause, Volume2, VolumeX, Maximize2, Download } from 'lucide-react';
import { useState, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface MediaPlayerProps {
    src: string;
    type: 'video' | 'audio';
    title?: string;
}

export function MediaPlayer({ src, type, title }: MediaPlayerProps) {
    const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [isMuted, setIsMuted] = useState(false);

    const togglePlay = () => {
        if (mediaRef.current) {
            if (isPlaying) {
                mediaRef.current.pause();
            } else {
                mediaRef.current.play();
            }
            setIsPlaying(!isPlaying);
        }
    };

    const toggleMute = () => {
        if (mediaRef.current) {
            mediaRef.current.muted = !isMuted;
            setIsMuted(!isMuted);
        }
    };

    const handleDownload = async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            const response = await fetch(src, { mode: 'cors' });
            if (!response.ok) throw new Error('Network response was not ok');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            const filename = src.split('/').pop()?.split('?')[0] || 'download'; // Clean filename
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error("Download failed or CORS blocked, falling back to new tab:", error);
            const link = document.createElement('a');
            link.href = src;
            link.target = '_blank';
            link.download = src.split('/').pop()?.split('?')[0] || 'download';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    };

    return (
        <div className={cn(
            "rounded-xl overflow-hidden border bg-black/5 my-2",
            type === 'video' ? "max-w-sm" : "max-w-xs"
        )}>
            <div className="relative group">
                {type === 'video' ? (
                    <video
                        ref={mediaRef as React.RefObject<HTMLVideoElement>}
                        src={src}
                        className="w-full aspect-video object-cover bg-black"
                        onClick={togglePlay}
                        onEnded={() => setIsPlaying(false)}
                    />
                ) : (
                    <audio
                        ref={mediaRef as React.RefObject<HTMLAudioElement>}
                        src={src}
                        className="hidden"
                        onEnded={() => setIsPlaying(false)}
                    />
                )}

                {/* Controls Overlay (for video) or Main UI (for audio) */}
                <div className={cn(
                    "flex items-center gap-2 p-2 transition-opacity",
                    type === 'video' ? "absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent text-white opacity-0 group-hover:opacity-100" : "bg-card text-foreground"
                )}>
                    <Button
                        variant="ghost"
                        size="icon"
                        className={cn("h-8 w-8", type === 'video' && "text-white hover:text-white hover:bg-white/20")}
                        onClick={togglePlay}
                    >
                        {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                    </Button>

                    <div className="flex-1 text-xs truncate font-medium px-2">
                        {title || (src.split('/').pop() ?? 'Media')}
                    </div>

                    <Button
                        variant="ghost"
                        size="icon"
                        className={cn("h-8 w-8", type === 'video' && "text-white hover:text-white hover:bg-white/20")}
                        onClick={handleDownload}
                        title="下载"
                    >
                        <Download className="w-4 h-4" />
                    </Button>

                    <Button
                        variant="ghost"
                        size="icon"
                        className={cn("h-8 w-8", type === 'video' && "text-white hover:text-white hover:bg-white/20")}
                        onClick={toggleMute}
                    >
                        {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
                    </Button>
                </div>
            </div>
        </div>
    );
}

export function ImagePreview({ src, alt }: { src: string; alt?: string }) {
    const [isOpen, setIsOpen] = useState(false);

    const handleDownload = async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            const response = await fetch(src, { mode: 'cors' });
            if (!response.ok) throw new Error('Network response was not ok');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            const filename = src.split('/').pop()?.split('?')[0] || 'image';
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error("Download failed or CORS blocked, falling back to new tab:", error);
            const link = document.createElement('a');
            link.href = src;
            link.target = '_blank';
            link.download = src.split('/').pop()?.split('?')[0] || 'image';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    };

    return (
        <>
            <div
                className="relative group cursor-zoom-in my-2 rounded-lg overflow-hidden border shadow-sm max-w-sm transition-transform hover:scale-[1.01]"
                onClick={() => setIsOpen(true)}
            >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    src={src}
                    alt={alt || "Image"}
                    className="w-full h-auto object-cover bg-muted/20"
                    loading="lazy"
                />

                {/* Hover Actions */}
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors opacity-0 group-hover:opacity-100 flex flex-col justify-between p-2">
                    <div className="flex justify-end">
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-white hover:bg-black/20 hover:text-white"
                            onClick={handleDownload}
                            title="下载图片"
                        >
                            <Download className="w-4 h-4 drop-shadow-md" />
                        </Button>
                    </div>
                    <div className="flex items-center justify-center">
                        <Maximize2 className="w-6 h-6 text-white drop-shadow-md opacity-80" />
                    </div>
                    <div className="h-8" /> {/* Spacer for balance if needed, or remove */}
                </div>
            </div>

            {isOpen && (
                <div
                    className="fixed inset-0 z-[100] flex items-center justify-center bg-black/90 backdrop-blur-sm p-4 animate-in fade-in duration-200"
                    onClick={() => setIsOpen(false)}
                >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                        src={src}
                        alt={alt || "Full preview"}
                        className="max-w-full max-h-full rounded shadow-2xl"
                    />
                    <Button
                        variant="ghost"
                        size="icon"
                        className="absolute top-4 right-4 text-white hover:bg-white/20"
                        onClick={handleDownload}
                    >
                        <Download className="w-6 h-6" />
                    </Button>
                </div>
            )}
        </>
    );
}
