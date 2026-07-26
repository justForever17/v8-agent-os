"use client";

import { Download, Layout, PanelRightOpen } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { createExternalArtifactDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";

interface HTMLFileCardProps {
    url: string;
    filename?: string;
    filesize?: string;
}
export function HTMLFileCard({ url, filename, filesize }: HTMLFileCardProps) {
    const t = useT();
    const sessionId = useWorkbenchStore((state) => state.sessionId);
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const displayFilename = filename || decodeURIComponent(url.split("/").pop()?.split("?")[0] || "document.html");
    const document = sessionId ? createExternalArtifactDocument({
        sessionId,
        id: `html:${url}`,
        title: displayFilename,
        url,
        renderer: "html",
        mimeType: "text/html",
    }) : null;
    return (
        <div data-v8-context-resource className="mt-2 flex h-11 w-full max-w-sm items-center gap-2 rounded-[5px] border border-border/65 bg-background/70 px-2.5">
            <Layout className="h-4 w-4 shrink-0 text-blue-600" />
            <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{displayFilename}</div>
                <div className="text-[10px] text-muted-foreground">{filesize || t("web.fileCard.htmlHint")}</div>
            </div>
            <button data-v8-context-open-workbench type="button" disabled={!document} onClick={() => document && openDocument(document, { activate: true, mode: "split" })} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40" aria-label={t("web.artifactCard.openWorkbench")}><PanelRightOpen className="h-3.5 w-3.5" /></button>
            <a href={url} download target="_blank" rel="noreferrer" className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.artifactCard.download")}><Download className="h-3.5 w-3.5" /></a>
        </div>
    );
}
