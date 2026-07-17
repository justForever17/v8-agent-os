"use client";

import { Disc3, FileAudio, FileImage, FileText, FileVideo, Layout, Code, Download, Maximize2, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

interface ArtifactCardProps {
    id: string;
    title: string;
    type: 'code' | 'markdown' | 'html' | 'image' | 'video' | 'audio' | 'music' | 'document' | 'file';
    subtitle?: string;
    className?: string;
    onClick?: () => void;
    onDownload?: () => void;
}

export function ArtifactCard({ title, type, subtitle, className, onClick, onDownload }: ArtifactCardProps) {
    const t = useT();
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
                        : type === 'music'
                            ? <Disc3 className="h-5 w-5" />
                            : type === 'audio'
                            ? <FileAudio className="h-5 w-5" />
                            : <Link2 className="h-5 w-5" />;

    return (
        <div className={cn("group my-1.5 flex min-h-11 items-center gap-2 rounded-[5px] border border-border/65 bg-background/70 px-2.5 py-1.5 transition-colors hover:border-primary/30", className)}>
            <div className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-[4px] border",
                type === 'code' ? "bg-blue-100/50 text-blue-600 border-blue-200" :
                    type === 'html' ? "bg-orange-100/50 text-orange-600 border-orange-200" :
                        type === 'image' ? "bg-pink-100/50 text-pink-600 border-pink-200" :
                            type === 'video' ? "bg-violet-100/50 text-violet-600 border-violet-200" :
                                type === 'music' ? "bg-fuchsia-100/50 text-fuchsia-600 border-fuchsia-200" :
                                    type === 'audio' ? "bg-amber-100/50 text-amber-600 border-amber-200" :
                                    "bg-green-100/50 text-green-600 border-green-200"
            )}>
                {icon}
            </div>

            <div className="min-w-0 flex-1 cursor-pointer" onClick={onClick}>
                <div className="truncate text-xs font-medium transition-colors group-hover:text-primary">{title}</div>
                <div className="truncate text-[10px] text-muted-foreground">{subtitle || t("web.artifactCard.defaultSubtitle", { type })}</div>
            </div>

            <div className="flex items-center gap-2">
                {onClick && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="hidden h-7 rounded-[4px] gap-1.5 px-2 text-[10px] text-muted-foreground hover:text-foreground sm:flex"
                        onClick={(e) => {
                            e.stopPropagation();
                            onClick();
                        }}
                    >
                        <Maximize2 className="h-3.5 w-3.5" />
                        {t("web.artifactCard.openWorkbench")}
                    </Button>
                )}
                {onDownload && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="hidden h-7 rounded-[4px] gap-1.5 px-2 text-[10px] text-muted-foreground hover:text-foreground sm:flex"
                        onClick={(e) => {
                            e.stopPropagation();
                            onDownload();
                        }}
                    >
                        <Download className="h-3.5 w-3.5" />
                        {t("web.artifactCard.download")}
                    </Button>
                )}
            </div>
        </div>
    );
}
