"use client";
/* eslint-disable @next/next/no-img-element -- session-authorized generated media */

import { useEffect, useRef, useState } from "react";
import { useT } from "@/components/providers/LocaleProvider";
import type { CreativeCanvasMediaResource } from "../CreativeCanvasMedia";

type ControlPack = {
    schema: string;
    lineage: { sessionId: string };
    preview?: { videoArtifactId?: string; imageArtifactId?: string };
    scene: { durationSeconds: number; entities: { entityId: string; name: string; proxyColor: string }[] };
    references: unknown[];
    files: { channel: string; artifactId: string; sha256: string }[];
};

export default function ControlPackPreview({ resource, compact, visible, active }: {
    resource: CreativeCanvasMediaResource; compact: boolean; visible: boolean; active: boolean;
}) {
    const t = useT();
    const [loaded, setLoaded] = useState<{ key: string; pack: ControlPack | null } | null>(null);
    const video = useRef<HTMLVideoElement | null>(null);
    const key = `${resource.sessionId}:${resource.id}:${resource.url}`;
    const pack = loaded?.key === key ? loaded.pack : null;
    useEffect(() => {
        if (!visible || !resource.url || !resource.sessionId) return;
        const request = new AbortController();
        void fetch(resource.url, { signal: request.signal, cache: "no-store" }).then(async (response) => {
            if (!response.ok) throw new Error("unavailable");
            const value = await response.json() as ControlPack;
            if (value.schema !== "v8.proxy_scene_control_pack.v1" || value.lineage?.sessionId !== resource.sessionId || !Array.isArray(value.scene?.entities)) throw new Error("scope");
            if (!request.signal.aborted) setLoaded({ key, pack: value });
        }).catch(() => { if (!request.signal.aborted) setLoaded({ key, pack: null }); });
        return () => request.abort();
    }, [key, resource.sessionId, resource.url, visible]);
    useEffect(() => { if (!active || !visible) video.current?.pause(); }, [active, visible]);
    if (!pack) return <div className="p-4 text-center text-xs text-muted-foreground">{t(loaded?.key === key ? "web.workbench.canvas.scene.packUnavailable" : "web.workbench.canvas.scene.packLoading")}</div>;
    const url = (id?: string) => id ? `/api/artifacts/${encodeURIComponent(id)}/content?sessionId=${encodeURIComponent(resource.sessionId || "")}` : undefined;
    return <div className="flex h-full w-full flex-col bg-muted/20">
        <div className="relative min-h-0 flex-1">{visible && pack.preview?.videoArtifactId ? <video ref={video} src={url(pack.preview.videoArtifactId)} controls={!compact} playsInline preload="metadata" poster={url(pack.preview.imageArtifactId)} className="h-full w-full object-contain" /> : pack.preview?.imageArtifactId ? <img src={url(pack.preview.imageArtifactId)} alt={resource.name} className="h-full w-full object-contain" /> : null}</div>
        {!compact ? <div className="space-y-2 px-3 py-2"><p className="text-[10px] text-muted-foreground">{t("web.workbench.canvas.scene.packLocal")}</p><div className="flex flex-wrap gap-2">{pack.scene.entities.map((entity) => <span key={entity.entityId} className="flex items-center gap-1.5 text-[10px]"><span className="h-2 w-2 rounded-full" style={{ background: entity.proxyColor }} />{entity.name}</span>)}</div><details className="text-[10px] text-muted-foreground"><summary className="cursor-pointer">{t("web.workbench.canvas.scene.packDetails")}</summary><div className="max-h-32 space-y-1 overflow-y-auto pt-2">{pack.files.map((file) => <div key={file.artifactId || file.channel} className="break-all">{file.channel} · {file.sha256}</div>)}</div></details></div> : null}
    </div>;
}
