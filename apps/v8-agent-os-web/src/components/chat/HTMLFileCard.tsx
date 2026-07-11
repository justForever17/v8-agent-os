"use client";

import { Download, Layout, PanelRightOpen } from "lucide-react";

import { createExternalArtifactDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";

interface HTMLFileCardProps {
    url: string;
    filename?: string;
    filesize?: string;
}
export function HTMLFileCard({ url, filename, filesize }: HTMLFileCardProps) {
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const displayFilename = filename || decodeURIComponent(url.split("/").pop()?.split("?")[0] || "document.html");
    const document = createExternalArtifactDocument({
        id: `html:${url}`,
        title: displayFilename,
        url,
        renderer: "html",
        mimeType: "text/html",
    });
    return (
        <div className="mt-2 flex h-11 w-full max-w-sm items-center gap-2 rounded-[5px] border border-border/65 bg-background/70 px-2.5">
            <Layout className="h-4 w-4 shrink-0 text-blue-600" />
            <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{displayFilename}</div>
                <div className="text-[10px] text-muted-foreground">{filesize || "HTML · 脚本默认禁用"}</div>
            </div>
            <button type="button" onClick={() => openDocument(document, { activate: true, mode: "split" })} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="在工作台打开"><PanelRightOpen className="h-3.5 w-3.5" /></button>
            <a href={url} download target="_blank" rel="noreferrer" className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="下载"><Download className="h-3.5 w-3.5" /></a>
        </div>
    );
}
