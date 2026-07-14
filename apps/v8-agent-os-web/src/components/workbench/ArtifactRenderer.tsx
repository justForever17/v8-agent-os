"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { Download, ExternalLink, FileWarning, SlidersHorizontal } from "lucide-react";

import { CodeBlock } from "@/components/chat/CodeBlock";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { useT } from "@/components/providers/LocaleProvider";
import { getWorkbenchDocumentPayload, type ArtifactWorkbenchDocument } from "@/lib/workbench";
import { normalizeRuntimeArtifact, resolveRuntimeArtifactUrl, type RuntimeArtifact } from "@/lib/artifacts";

const ModelViewer = dynamic(() => import("@/components/chat/ModelViewer").then((mod) => mod.ModelViewer), {
    ssr: false,
    loading: () => <div className="flex h-full items-center justify-center text-xs text-muted-foreground">正在加载 3D 预览…</div>,
});

function safeHtmlPreview(content: string) {
    const csp = "default-src 'none'; img-src data: blob:; media-src data: blob:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'";
    const withoutScripts = content.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
    const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
    return /<head[\s>]/i.test(withoutScripts)
        ? withoutScripts.replace(/<head([^>]*)>/i, `<head$1>${meta}`)
        : `<!doctype html><html><head>${meta}<meta charset="utf-8"></head><body>${withoutScripts}</body></html>`;
}
function ArtifactMetadata({ artifact, document }: { artifact: RuntimeArtifact | null; document: ArtifactWorkbenchDocument }) {
    if (!artifact) return null;
    return (
        <div className="grid gap-x-6 gap-y-2 border-t border-border/60 px-3 py-2 text-[10px] text-muted-foreground sm:grid-cols-2">
            <div className="truncate"><span className="text-foreground/70">类型</span> · {artifact?.mimeType || artifact?.kind || document.renderer}</div>
            {artifact?.workspaceRelativePath ? <div className="truncate sm:col-span-2"><span className="text-foreground/70">工作区</span> · {artifact.workspaceRelativePath}</div> : null}
        </div>
    );
}

function codeLanguage(document: ArtifactWorkbenchDocument, artifact: RuntimeArtifact | null) {
    const explicit = String(getWorkbenchDocumentPayload(document.documentId)?.language || "").trim().toLowerCase();
    if (explicit) return explicit;
    const title = String(artifact?.workspaceRelativePath || artifact?.sourcePath || document.title || "");
    const extension = title.split(".").at(-1)?.toLowerCase() || "";
    const aliases: Record<string, string> = {
        ts: "typescript",
        tsx: "tsx",
        js: "javascript",
        jsx: "jsx",
        py: "python",
        ps1: "powershell",
        yml: "yaml",
        patch: "diff",
        diff: "diff",
        sh: "bash",
    };
    return aliases[extension] || extension || "text";
}

export function ArtifactRenderer({ document }: { document: ArtifactWorkbenchDocument }) {
    const t = useT();
    const cached = getWorkbenchDocumentPayload(document.documentId);
    const [artifact, setArtifact] = useState<RuntimeArtifact | null>(cached?.artifact || null);
    const [text, setText] = useState(cached?.inlineContent || "");
    const [loading, setLoading] = useState(!cached?.artifact && !cached?.inlineContent && !cached?.resourceUrl);
    const [error, setError] = useState("");

    useEffect(() => {
        if (artifact || cached?.inlineContent || cached?.resourceUrl) return;
        let cancelled = false;
        void fetch(`/api/artifacts/${encodeURIComponent(document.subjectRef.artifactId)}`, { cache: "no-store" })
            .then(async (response) => {
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                const normalized = normalizeRuntimeArtifact(payload);
                if (!normalized) throw new Error("无法解析产物信息。");
                if (!cancelled) setArtifact(normalized);
            })
            .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [artifact, cached?.inlineContent, cached?.resourceUrl, document.subjectRef.artifactId]);

    const resourceUrl = useMemo(
        () => cached?.resourceUrl || (artifact ? resolveRuntimeArtifactUrl(artifact) : undefined) || "",
        [artifact, cached?.resourceUrl],
    );
    const uiPatchUrl = useMemo(() => {
        const artifactPath = String(artifact?.workspaceRelativePath || artifact?.workspacePath || "").trim();
        const artifactSessionId = String(document.subjectRef.sessionId || artifact?.sessionId || "").trim();
        if (document.renderer !== "html" || !artifactPath || !artifactSessionId) return "";
        const params = new URLSearchParams({
            sessionId: artifactSessionId,
            entryPath: artifactPath,
            returnTo: `/chat?id=${encodeURIComponent(artifactSessionId)}`,
        });
        return `/ui-patch?${params.toString()}`;
    }, [artifact?.sessionId, artifact?.workspacePath, artifact?.workspaceRelativePath, document.renderer, document.subjectRef.sessionId]);

    useEffect(() => {
        if (text || !resourceUrl || !["code", "text", "markdown", "html"].includes(document.renderer)) return;
        let cancelled = false;
        void fetch(resourceUrl, { cache: "no-store" })
            .then(async (response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const next = await response.text();
                if (!cancelled) setText(next.slice(0, 2 * 1024 * 1024));
            })
            .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
        return () => { cancelled = true; };
    }, [document.renderer, resourceUrl, text]);

    if (loading) return <div className="flex h-full items-center justify-center text-xs text-muted-foreground">正在加载产物…</div>;
    if (error && !resourceUrl && !text) {
        return <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-sm text-muted-foreground"><FileWarning className="h-6 w-6" /><span>{error}</span></div>;
    }

    const preview = (() => {
        if (document.renderer === "image" && resourceUrl) {
            // eslint-disable-next-line @next/next/no-img-element
            return <div className="flex h-full items-center justify-center bg-black/5 p-2"><img src={resourceUrl} alt={document.title} className="max-h-full max-w-full object-contain" /></div>;
        }
        if (document.renderer === "video" && resourceUrl) return <div className="flex h-full items-center justify-center bg-black"><video src={resourceUrl} controls className="max-h-full w-full" /></div>;
        if (document.renderer === "audio" && resourceUrl) return <div className="flex h-full items-center justify-center px-8"><audio src={resourceUrl} controls className="w-full max-w-2xl" /></div>;
        if (document.renderer === "pdf" && resourceUrl) return <iframe title={document.title} src={resourceUrl} className="h-full w-full border-0 bg-white" />;
        if (document.renderer === "model_3d" && resourceUrl) return <ModelViewer src={resourceUrl} className="h-full min-h-[420px] w-full rounded-none border-0" />;
        if (document.renderer === "html" && text) return <iframe title={document.title} srcDoc={safeHtmlPreview(text)} sandbox="" className="h-full w-full border-0 bg-white" />;
        if (document.renderer === "markdown" && text) return <article className="mx-auto max-w-3xl px-6 py-5"><MarkdownRenderer content={text} surface="document" /></article>;
        if ((document.renderer === "code" || document.renderer === "text") && text) {
            return <div className="min-h-full bg-[#0d1117] p-3"><CodeBlock language={codeLanguage(document, artifact)} value={text} /></div>;
        }
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center text-sm text-muted-foreground">
                <FileWarning className="h-7 w-7" />
                <div className="font-medium text-foreground">暂无可用的本地预览</div>
                <div>该类型首期提供下载或系统打开，不会把私有地址发送给第三方在线 Viewer。</div>
            </div>
        );
    })();

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="flex h-8 shrink-0 items-center justify-end gap-1 border-b border-border/60 px-2">
                {uiPatchUrl ? <a href={uiPatchUrl} className="inline-flex h-6 items-center gap-1 rounded-sm px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label={t("web.uiPatch.open")}><SlidersHorizontal className="h-3.5 w-3.5" />{t("web.uiPatch.openShort")}</a> : null}
                {resourceUrl ? <a href={resourceUrl} target="_blank" rel="noreferrer" className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="在新窗口打开"><ExternalLink className="h-3.5 w-3.5" /></a> : null}
                {resourceUrl ? <a href={resourceUrl} download className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="下载产物"><Download className="h-3.5 w-3.5" /></a> : null}
            </div>
            <div className="min-h-0 flex-1 overflow-auto">{preview}</div>
            <ArtifactMetadata artifact={artifact} document={document} />
        </div>
    );
}
