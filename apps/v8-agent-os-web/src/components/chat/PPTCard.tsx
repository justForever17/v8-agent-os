"use client";

import { Download, FileText, PanelRightOpen } from "lucide-react";

import { createExternalArtifactDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";

interface PPTCardProps {
    url: string;
    filename?: string;
    filesize?: string;
}
export function PPTCard({ url, filename, filesize }: PPTCardProps) {
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const displayFilename = filename || decodeURIComponent(url.split("/").pop()?.split("?")[0] || "Presentation.pptx");
    const document = createExternalArtifactDocument({
        id: `ppt:${url}`,
        title: displayFilename,
        url,
        renderer: "download",
        mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    });
    return (
        <div className="mt-2 flex h-11 w-full max-w-sm items-center gap-2 rounded-[5px] border border-border/65 bg-background/70 px-2.5">
            <FileText className="h-4 w-4 shrink-0 text-orange-600" />
            <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{displayFilename}</div>
                <div className="text-[10px] text-muted-foreground">{filesize || "PPTX · 本地无幻灯片预览时仅下载/系统打开"}</div>
            </div>
            <button type="button" onClick={() => openDocument(document, { activate: true, mode: "split" })} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="在工作台打开"><PanelRightOpen className="h-3.5 w-3.5" /></button>
            <a href={url} download target="_blank" rel="noreferrer" className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="下载"><Download className="h-3.5 w-3.5" /></a>
        </div>
    );
}
