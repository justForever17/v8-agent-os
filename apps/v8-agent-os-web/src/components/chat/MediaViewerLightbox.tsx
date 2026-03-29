"use client";

import React, { useEffect, useState } from 'react';
import { X, ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export interface MediaItem {
    type: 'image' | 'video';
    src: string; // URL or ObjectURL
    name?: string;
    file?: File; // Optional original file reference
}

interface MediaViewerLightboxProps {
    items: MediaItem[];
    initialIndex?: number;
    isOpen: boolean;
    onClose: () => void;
}

export function MediaViewerLightbox({ items, initialIndex = 0, isOpen, onClose }: MediaViewerLightboxProps) {
    // When initialIndex changes from parents (like clicking a different photo),
    // we want to adopt that as starting index.
    const [currentIndex, setCurrentIndex] = useState(initialIndex);
    const [prevProps, setPrevProps] = useState({ isOpen, initialIndex });
    
    // Sync state when props change, avoiding standard effects for derived state 
    // to prevent cascading renders as per React 18 best practices
    if (isOpen !== prevProps.isOpen || initialIndex !== prevProps.initialIndex) {
        setPrevProps({ isOpen, initialIndex });
        if (isOpen) {
            setCurrentIndex(initialIndex);
        }
    }

    const [isAnimating] = useState(false); // Kept for future expandability, but currently unused

    // Sync initialIndex when opening - avoiding inline sync setState
    useEffect(() => {
        if (isOpen) {
            document.body.style.overflow = 'hidden'; // Prevent background scrolling
        } else {
            document.body.style.overflow = '';
        }
        return () => {
            document.body.style.overflow = '';
        };
    }, [isOpen, initialIndex]);

    // Handle keyboard navigation
    useEffect(() => {
        if (!isOpen) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
            if (e.key === 'ArrowLeft') {
                if (currentIndex > 0) setCurrentIndex(curr => curr - 1);
            }
            if (e.key === 'ArrowRight') {
                if (currentIndex < items.length - 1) setCurrentIndex(curr => curr + 1);
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, currentIndex, items.length, onClose]);

    if (!isOpen || items.length === 0) return null;

    const handlePrev = () => {
        if (currentIndex > 0) setCurrentIndex(curr => curr - 1);
    };

    const handleNext = () => {
        if (currentIndex < items.length - 1) setCurrentIndex(curr => curr + 1);
    };

    const handleDownload = () => {
        const item = items[currentIndex];
        const a = document.createElement('a');
        a.href = item.src;
        a.download = item.name || `media-${currentIndex}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    };

    const currentItem = items[currentIndex];

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-md animate-in fade-in duration-300">
            {/* Top Bar */}
            <div className="absolute top-0 left-0 right-0 p-4 flex justify-between items-center z-50 bg-gradient-to-b from-black/50 to-transparent">
                <div className="text-white/80 text-sm font-medium">
                    {items.length > 1 ? `${currentIndex + 1} / ${items.length}` : ''}
                    {currentItem.name && <span className="ml-4 opacity-70">{currentItem.name}</span>}
                </div>
                <div className="flex items-center gap-2">
                    <Button 
                        variant="ghost" 
                        size="icon" 
                        className="text-white/80 hover:text-white hover:bg-white/10 rounded-full"
                        onClick={handleDownload}
                        title="Download"
                    >
                        <Download className="w-5 h-5" />
                    </Button>
                    <Button 
                        variant="ghost" 
                        size="icon" 
                        className="text-white/80 hover:text-white hover:bg-white/10 rounded-full"
                        onClick={onClose}
                        title="Close (Esc)"
                    >
                        <X className="w-6 h-6" />
                    </Button>
                </div>
            </div>

            {/* Navigation Areas */}
            {currentIndex > 0 && (
                <div 
                    className="absolute left-0 top-0 bottom-0 w-1/6 min-w-[60px] cursor-pointer flex items-center justify-start px-4 z-40 group hover:bg-gradient-to-r hover:from-black/20 hover:to-transparent transition-all"
                    onClick={handlePrev}
                >
                    <div className="p-3 rounded-full bg-black/20 text-white/50 group-hover:bg-black/60 group-hover:text-white transition-all backdrop-blur-sm -translate-x-4 group-hover:translate-x-0">
                        <ChevronLeft className="w-8 h-8" />
                    </div>
                </div>
            )}

            {currentIndex < items.length - 1 && (
                <div 
                    className="absolute right-0 top-0 bottom-0 w-1/6 min-w-[60px] cursor-pointer flex items-center justify-end px-4 z-40 group hover:bg-gradient-to-l hover:from-black/20 hover:to-transparent transition-all"
                    onClick={handleNext}
                >
                    <div className="p-3 rounded-full bg-black/20 text-white/50 group-hover:bg-black/60 group-hover:text-white transition-all backdrop-blur-sm translate-x-4 group-hover:translate-x-0">
                        <ChevronRight className="w-8 h-8" />
                    </div>
                </div>
            )}

            {/* Main Content Area */}
            <div 
                className={cn(
                    "relative w-full h-full p-12 flex items-center justify-center transition-all duration-300",
                    isAnimating ? "opacity-50 scale-95" : "opacity-100 scale-100"
                )}
                onClick={(e) => {
                    // Close if clicking the background, not the image itself
                    if (e.target === e.currentTarget) onClose();
                }}
            >
                {currentItem.type === 'video' ? (
                    <video 
                        key={currentItem.src}
                        src={currentItem.src} 
                        className="max-w-full max-h-full rounded-md shadow-2xl object-contain animate-in zoom-in-95 duration-300" 
                        controls 
                        autoPlay 
                        onClick={(e) => e.stopPropagation()}
                    />
                ) : (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img 
                        key={currentItem.src}
                        src={currentItem.src} 
                        alt={currentItem.name || "Preview"} 
                        className="max-w-full max-h-full rounded-md shadow-2xl object-contain animate-in zoom-in-95 duration-300"
                        onClick={(e) => e.stopPropagation()}
                    />
                )}
            </div>
        </div>
    );
}
