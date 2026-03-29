"use client";

import { FileAudio, FileImage, FileText, FileVideo, Layout, Code, Download, Maximize2, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface ArtifactCardProps {
    id: string;
    title: string;
    type: 'code' | 'markdown' | 'html' | 'image' | 'video' | 'audio' | 'document' | 'file';
    subtitle?: string;
    className?: string;
    onClick?: () => void;
    onDownload?: () => void;
}

export function ArtifactCard({ title, type, subtitle, className, onClick, onDownload }: ArtifactCardProps) {
    const icon = type === 'code'
        ? <Code className="h-5 w-5" />
        : type === 'html'
            ? <Layout className="h-5 w-5" />
            : type === 'markdown' || type === 'document'
                ? <FileText className="h-5 w-5" />
                : type === 'image'
                    ? <FileImage className="h-5 w-5" />
                    : type === 'video'
                        ? <FileVideo className="h-5 w-5" />
                        : type === 'audio'
                            ? <FileAudio className="h-5 w-5" />
                            : <Link2 className="h-5 w-5" />;

    return (
        <div
            className={cn(
                "flex items-center gap-3 p-3 my-2 rounded-lg border bg-card transition-colors select-none group",
                className
            )}
        >
            <div className={cn(
                "h-10 w-10 shrink-0 rounded-md flex items-center justify-center border shadow-sm",
                type === 'code' ? "bg-blue-100/50 text-blue-600 border-blue-200" :
                    type === 'html' ? "bg-orange-100/50 text-orange-600 border-orange-200" :
                        type === 'image' ? "bg-pink-100/50 text-pink-600 border-pink-200" :
                            type === 'video' ? "bg-violet-100/50 text-violet-600 border-violet-200" :
                                type === 'audio' ? "bg-amber-100/50 text-amber-600 border-amber-200" :
                                    "bg-green-100/50 text-green-600 border-green-200"
            )}>
                {icon}
            </div>

            <div className="flex-1 min-w-0 cursor-pointer" onClick={onClick}>
                <div className="font-medium text-sm truncate group-hover:text-primary transition-colors">{title}</div>
                <div className="text-xs text-muted-foreground">{subtitle || `${type} Artifact`}</div>
            </div>

            <div className="flex items-center gap-2">
                {onClick && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-8 gap-1.5 text-xs text-muted-foreground hover:text-foreground hidden sm:flex"
                        onClick={(e) => {
                            e.stopPropagation();
                            onClick();
                        }}
                    >
                        <Maximize2 className="h-3.5 w-3.5" />
                        预览
                    </Button>
                )}
                {onDownload && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-8 gap-1.5 text-xs text-muted-foreground hover:text-foreground hidden sm:flex"
                        onClick={(e) => {
                            e.stopPropagation();
                            onDownload();
                        }}
                    >
                        <Download className="h-3.5 w-3.5" />
                        下载
                    </Button>
                )}
            </div>
        </div>
    );
}
