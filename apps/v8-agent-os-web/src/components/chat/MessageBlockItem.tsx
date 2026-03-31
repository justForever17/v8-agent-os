import { memo } from "react";
import dynamic from "next/dynamic";
import { ContentBlock } from "@/lib/chat/content-detector";
import { ArtifactCard } from "./ArtifactCard";
import { CodeBlock } from "./CodeBlock";
import { PPTCard } from "./PPTCard";
import { HTMLFileCard } from "./HTMLFileCard";
import { MermaidRenderer } from "./MermaidRenderer";
import { VoiceCard } from "./VoiceCard";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { useChatStore } from "@/store/chat-store";

const ModelViewer = dynamic(
    () => import("./ModelViewer").then((mod) => mod.ModelViewer),
    {
        ssr: false,
        loading: () => (
            <div className="flex aspect-video w-full items-center justify-center rounded-lg border border-border bg-muted/30 text-sm text-muted-foreground">
                正在加载 3D 预览...
            </div>
        ),
    }
);

interface MessageBlockItemProps {
    block: ContentBlock;
}

export const MessageBlockItem = memo(({ block }: MessageBlockItemProps) => {
    const setActiveArtifactId = useChatStore(s => s.setActiveArtifactId);
    if (block.type === 'file-ppt') {
        return <PPTCard url={block.data?.url || ''} filename={block.data?.filename} />;
    }
    if (block.type === 'file-html') {
        return <HTMLFileCard url={block.data?.url || ''} filename={block.data?.filename} />;
    }
    if (block.type === 'artifact') {
        const title = block.data?.title || 'Generated Artifact';
        const type = block.data?.type || 'html';
        const handleDownload = () => {
            if (!block.content) return;
            const ext = type === 'html' ? 'html' : type === 'code' ? 'txt' : 'md';
            const blob = new Blob([block.content], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = `${title.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.${ext}`; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
        };
        return (
            <div className="flex flex-col gap-2 my-2">
                <CodeBlock language={type} value={block.content} isStreaming={Boolean(block.isStreaming)} />
                {!block.isStreaming && <ArtifactCard id={block.id} title={title} type={type} onClick={() => setActiveArtifactId(block.id)} onDownload={block.content ? handleDownload : undefined} />}
            </div>
        );
    }
    if (block.type === 'code' || block.type === 'html_snippet') {
        return <CodeBlock language={block.data?.language || 'text'} value={block.content} className="my-2" isStreaming={Boolean(block.isStreaming)} />;
    }
    if (block.type === 'mermaid') {
        return <div className="w-full my-3 overflow-x-auto"><MermaidRenderer code={block.content} /></div>;
    }
    if (block.type === 'model-3d') {
        return <div className="w-full my-4"><ModelViewer src={block.content} /><div className="mt-1 text-xs text-muted-foreground text-center"><a href={block.content} target="_blank" rel="noopener noreferrer" className="hover:underline">Download Model</a></div></div>;
    }
    if (block.type === 'voice') {
        return <VoiceCard content={block.content} isStreaming={Boolean(block.isStreaming)} />;
    }
    return <MarkdownRenderer content={block.content} />;
});

MessageBlockItem.displayName = "MessageBlockItem";
