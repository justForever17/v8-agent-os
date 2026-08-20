import { memo, useEffect, useMemo, useState, ComponentPropsWithoutRef } from 'react';
import { cn } from "@/lib/utils";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { motion } from 'framer-motion';
import { Box, Download, ExternalLink, FileText } from 'lucide-react';
import { coerceAdminResourceRef, resolveAdminResourceUrl } from "@v8/session-realtime";
import { MediaPlayer, ImagePreview } from './MediaRenderers';
import { CodeBlock as SharedCodeBlock } from './CodeBlock';
import { PPTCard } from './PPTCard';
import { useDebouncedValue } from '@/hooks/use-debounce';
import { createExternalArtifactDocument } from '@/lib/workbench';
import { useT } from '@/components/providers/LocaleProvider';
import { decodeWorkbenchFileHref, resolveAndOpenWorkspaceFile } from '@/lib/workbench-actions';
import { useWorkbenchStore } from '@/store/workbench-store';
import { artifactDownloadUrl } from '@/lib/artifact-download';

// 宽松的媒体文件扩展名检测 (允许 URL 后带有参数, 如 ?token=...)
const MEDIA_EXTENSIONS = {
    video: /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i,
    audio: /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i,
    image: /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i,
};

const DOCUMENT_EXTENSIONS = {
    pdf: /\.(pdf)(\?.*)?$/i,
    ppt: /\.(pptx?|ppsx?)(\?.*)?$/i,
    model3d: /\.(glb|gltf|obj|fbx|stl|usd|usdz|zip)(\?.*)?$/i,
};

// 常见云存储域名的特殊处理 (即使没有后缀也尝试识别)
const CLOUD_IMAGE_DOMAINS = [
    'byteimg.com',   // ByteDance / Volcengine
    'volces.com',    // Volcengine
    'douyinpic.com', // Douyin
    'myqcloud.com',  // Tencent Cloud COS
    'aliyuncs.com',  // Aliyun OSS
    'amazonaws.com', // AWS S3
    'googleapis.com' // Google Cloud Storage
];

// 判断是否是图片 URL 的增强逻辑
function isImageUrl(url: string): boolean {
    if (!url) return false;

    // 1. 标准后缀检测 (忽略参数)
    if (MEDIA_EXTENSIONS.image.test(url)) return true;

    // 2. 检查 Query String 参数中是否有明显的图片提示 (如 .image)
    if (url.includes('.image?')) return true;

    // 3. 检查是否是已知的云图片服务域名 (且 URL 看起来像个文件路径)
    try {
        const urlObj = new URL(url);
        if (CLOUD_IMAGE_DOMAINS.some(domain => urlObj.hostname.includes(domain))) {
            // 如果是云存储域名，且没有明显的视频/音频特征，我们倾向于它是图片
            // (这是基于聊天场景的启发式推断)
            const isVideo = MEDIA_EXTENSIONS.video.test(url);
            const isAudio = MEDIA_EXTENSIONS.audio.test(url);
            if (!isVideo && !isAudio) {
                return true;
            }
        }
    } catch {
        // Invalid URL, ignore
    }

    return false;
}

// 嵌入式视频平台
const VIDEO_PLATFORMS = [
    { name: 'bilibili', regex: /bilibili\.com\/video\/(BV[\w]+)/i, embed: (id: string) => `//player.bilibili.com/player.html?bvid=${id}&high_quality=1` },
    { name: 'youtube', regex: /(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/i, embed: (id: string) => `https://www.youtube.com/embed/${id}` },
    { name: 'douyin', regex: /douyin\.com\/video\/(\d+)/i, embed: (id: string) => `https://www.douyin.com/player/video/${id}` },
];

/**
 * 预处理内容：将纯 URL 转换为 Markdown 链接格式
 * 这样 react-markdown 的 a 组件就能正确检测并渲染媒体
 */
let cachedWorkspaceAssetBaseUrl = "";
let workspaceAssetBaseRequest: Promise<string> | null = null;

async function loadWorkspaceAssetBaseUrl() {
    if (cachedWorkspaceAssetBaseUrl) {
        return cachedWorkspaceAssetBaseUrl;
    }
    if (!workspaceAssetBaseRequest) {
        workspaceAssetBaseRequest = fetch("/api/runtime/bridge", { cache: "no-store" })
            .then(async (response) => {
                if (!response.ok) {
                    throw new Error("读取引擎桥接配置失败");
                }
                const payload = (await response.json()) as { workspaceAssetBaseUrl?: string };
                cachedWorkspaceAssetBaseUrl = String(payload.workspaceAssetBaseUrl || "").trim().replace(/\/+$/, "");
                return cachedWorkspaceAssetBaseUrl;
            })
            .catch(() => "")
            .finally(() => {
                workspaceAssetBaseRequest = null;
            });
    }
    return workspaceAssetBaseRequest;
}

function preprocessContent(content: string, workspaceAssetBaseUrl: string): string {
    const linkedWorkspacePaths = content
        .replace(/workspace:\/\/([^\s<>()"'`]+)/gi, (_match, path) => `[${String(path).split(/[\\/]/).pop() || path}](#v8-workbench-file=${encodeURIComponent(path)})`)
        .replace(/(?<![\w:/])([A-Za-z]:[\\/][^\s<>()"'`]+\.[A-Za-z0-9]{1,10})(?=$|[\s,;)])/g, (_match, path) => `[${String(path).split(/[\\/]/).pop() || path}](#v8-workbench-file=${encodeURIComponent(path)})`);
    // 0. 将本地工作区路径自动转换为引擎的 Web 访问 URL
    // 支持匹配: E:\...\workspace\file.png 或 /usr/...\workspace\file.png 或单纯的 workspace/file.png
    let processed = linkedWorkspacePaths.replace(
        /(?:file:\/\/\/?)?(?:[a-zA-Z]:[\\/][\w.\\/-]*[\\/]workspace[\\/]|(?:\/[\w.-]+)+[\\/]workspace[\\/]|(?<![\w\/-])workspace[\\/])([^\s<>"'`\])]+)/gi,
        (match, filepath) => {
            if (!workspaceAssetBaseUrl) {
                return match;
            }
            const cleanPath = filepath.replace(/\\/g, '/');
            return `${workspaceAssetBaseUrl}/${cleanPath}`;
        }
    );

    // 1. Unwrap code-blocked URLs (e.g. `https://example.com/image.png`)
    // Many agents output URLs inside backticks, which prevents them from rendering as images.
    processed = processed.replace(
        /(?<!\\)`(https?:\/\/[^\s<>"'`)]+)(?<!\\)`/gi,
        (match, url) => {
            if (isImageUrl(url)) return `[image](${url})`;
            if (MEDIA_EXTENSIONS.video.test(url)) return `[video](${url})`;
            if (MEDIA_EXTENSIONS.audio.test(url)) return `[audio](${url})`;
            return match; // Keep as code if not media
        }
    );

    // 2. Match pure URLs (not in Markdown links and NOT in code blocks anymore)
    // Exclude backticks from URL match to avoid consuming trailing code fences
    return processed.replace(
        /(?<!\]\()(?<!\[.*\]\()(?<!\!\[.*\]\()(https?:\/\/[^\s<>"'`)]+)/gi,
        (match) => {
            // Check video platforms
            for (const platform of VIDEO_PLATFORMS) {
                if (platform.regex.test(match)) {
                    return `[${platform.name} video](${match})`;
                }
            }

            // Check images
            if (isImageUrl(match)) {
                return `[image](${match})`;
            }

            // Check other media
            if (MEDIA_EXTENSIONS.video.test(match)) return `[video](${match})`;
            if (MEDIA_EXTENSIONS.audio.test(match)) return `[audio](${match})`;

            // Plain URL
            return match;
        }
    );
}

function normalizeSurfaceHref(href: string) {
    const raw = String(href || "").trim();
    if (!raw) {
        return "";
    }
    return resolveAdminResourceUrl("web", undefined, coerceAdminResourceRef(raw))
        || raw.replace(/^\/api\/client\b/i, "/api");
}

function decodeFilenameFromUrl(url: string, fallback: string) {
    try {
        const parsed = new URL(url, typeof window !== "undefined" ? window.location.origin : "http://localhost");
        const last = parsed.pathname.split("/").filter(Boolean).pop();
        return last ? decodeURIComponent(last) : fallback;
    } catch {
        const last = String(url || "").split("?")[0].split(/[\\/]/).filter(Boolean).pop();
        return last ? decodeURIComponent(last) : fallback;
    }
}

function classifyDocumentUrl(url: string): "pdf" | "ppt" | "model3d" | "" {
    if (DOCUMENT_EXTENSIONS.ppt.test(url)) return "ppt";
    if (DOCUMENT_EXTENSIONS.pdf.test(url)) return "pdf";
    if (DOCUMENT_EXTENSIONS.model3d.test(url)) return "model3d";
    return "";
}

function DocumentLinkCard({
    href,
    label,
    type,
}: {
    href: string;
    label: string;
    type: "pdf" | "model3d";
}) {
    const t = useT();
    const sessionId = useWorkbenchStore((state) => state.sessionId);
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const filename = decodeFilenameFromUrl(href, label || (type === "pdf" ? "document.pdf" : "model.glb"));
    const document = sessionId ? createExternalArtifactDocument({
        sessionId,
        id: `${type}:${href}`,
        title: filename,
        url: href,
        renderer: type === "model3d" ? "model_3d" : "pdf",
        mimeType: type === "model3d" ? "model/gltf-binary" : "application/pdf",
    }) : null;
    const icon = type === "model3d" ? <Box className="h-5 w-5" /> : <FileText className="h-5 w-5" />;
    return (
        <div data-v8-context-resource className="my-2 flex w-full max-w-sm items-center gap-3 rounded-xl border border-border/70 bg-card/95 p-3 shadow-sm">
            <div className={cn(
                "flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border",
                type === "model3d"
                    ? "border-sky-200 bg-sky-100/60 text-sky-700"
                    : "border-red-200 bg-red-100/60 text-red-700",
            )}>
                {icon}
            </div>
            <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-foreground" title={filename}>
                    {filename}
                </div>
                <div className="text-xs text-muted-foreground">
                    {type === "model3d" ? t("web.markdown.model3d") : t("web.markdown.pdf")}
                </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
                <button
                    data-v8-context-open-workbench
                    type="button"
                    onClick={() => document && openDocument(document, { activate: true, mode: "split" })}
                    disabled={!document}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
                    title={t("web.artifactCard.openWorkbench")}
                >
                    <ExternalLink className="h-4 w-4" />
                </button>
                <a
                    href={artifactDownloadUrl(href)}
                    download
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
                    title={t("web.artifactCard.download")}
                >
                    <Download className="h-4 w-4" />
                </a>
            </div>
        </div>
    );
}

/**
 * 嵌入式视频组件
 */
function EmbeddedVideo({ url }: { url: string }) {
    for (const platform of VIDEO_PLATFORMS) {
        const match = url.match(platform.regex);
        if (match && match[1]) {
            const embedUrl = platform.embed(match[1]);
            return (
                <div className="aspect-video rounded-lg overflow-hidden my-2 border shadow-sm max-w-md">
                    <iframe
                        src={embedUrl}
                        className="w-full h-full"
                        allowFullScreen
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    />
                </div>
            );
        }
    }
    return null;
}

// Adapter to bridge ReactMarkdown props to our CodeBlock component
const CodeBlock = memo(({ inline, className, children, ...props }: ComponentPropsWithoutRef<'code'> & { inline?: boolean }) => {
    const match = /language-(\w+)/.exec(className || '');
    const language = match ? match[1] : 'text';
    const codeContent = String(children).replace(/\n$/, '');

    if (!inline && match) {
        return (
            <SharedCodeBlock
                language={language}
                value={codeContent}
                className={className}
            />
        );
    }

    return (
        <code className={cn("bg-muted/50 px-1.5 py-0.5 rounded font-mono text-sm border border-border/50", className)} {...props}>
            {children}
        </code>
    );
});

CodeBlock.displayName = 'CodeBlock';

interface MarkdownRendererProps {
    content: string;
    searchQuery?: string;
    surface?: "message" | "document";
}

type MarkdownAstNode = {
    type?: string;
    tagName?: string;
    value?: string;
    properties?: Record<string, unknown>;
    children?: MarkdownAstNode[];
};

function createSearchHighlightPlugin(query: string) {
    const needle = query.trim().toLowerCase();
    return () => (tree: MarkdownAstNode) => {
        if (!needle) return;
        const visit = (node: MarkdownAstNode, blocked = false) => {
            const nextBlocked = blocked || node.tagName === "code" || node.tagName === "pre";
            if (!Array.isArray(node.children)) return;
            const nextChildren: MarkdownAstNode[] = [];
            for (const child of node.children) {
                if (!nextBlocked && child.type === "text" && typeof child.value === "string") {
                    const source = child.value;
                    const lower = source.toLowerCase();
                    let cursor = 0;
                    let matchAt = lower.indexOf(needle, cursor);
                    while (matchAt >= 0) {
                        if (matchAt > cursor) nextChildren.push({ type: "text", value: source.slice(cursor, matchAt) });
                        nextChildren.push({
                            type: "element",
                            tagName: "mark",
                            properties: {
                                "data-workbench-search-match": "true",
                                className: ["rounded-sm", "bg-amber-300/35", "px-0.5", "data-[active=true]:bg-amber-400/80"],
                            },
                            children: [{ type: "text", value: source.slice(matchAt, matchAt + needle.length) }],
                        });
                        cursor = matchAt + needle.length;
                        matchAt = lower.indexOf(needle, cursor);
                    }
                    if (cursor > 0) {
                        if (cursor < source.length) nextChildren.push({ type: "text", value: source.slice(cursor) });
                        continue;
                    }
                }
                visit(child, nextBlocked);
                nextChildren.push(child);
            }
            node.children = nextChildren;
        };
        visit(tree);
    };
}

export const MarkdownRenderer = memo(({ content, searchQuery = "", surface = "message" }: MarkdownRendererProps) => {
    // 防抖 10ms 避免高频流式更新
    const debouncedContent = useDebouncedValue(content, 10);
    const [workspaceAssetBaseUrl, setWorkspaceAssetBaseUrl] = useState(() => cachedWorkspaceAssetBaseUrl);
    const sessionId = useWorkbenchStore((state) => state.sessionId);

    useEffect(() => {
        if (surface === "document") return;
        let active = true;
        void loadWorkspaceAssetBaseUrl().then((next) => {
            if (!active || !next || next === workspaceAssetBaseUrl) {
                return;
            }
            setWorkspaceAssetBaseUrl(next);
        });
        return () => {
            active = false;
        };
    }, [surface, workspaceAssetBaseUrl]);

    // 预处理：将纯 URL 转换为 Markdown 链接
    const processedContent = useMemo(
        () => surface === "document"
            ? debouncedContent
            : preprocessContent(debouncedContent, workspaceAssetBaseUrl),
        [debouncedContent, surface, workspaceAssetBaseUrl]
    );
    const rehypePlugins = useMemo(
        () => searchQuery.trim() ? [createSearchHighlightPlugin(searchQuery)] : [],
        [searchQuery],
    );

    const components = useMemo(() => ({
        pre: ({ children }: ComponentPropsWithoutRef<'pre'>) => <>{children}</>,
        p: ({ children }: ComponentPropsWithoutRef<'p'>) => (
            surface === "document"
                ? <p className="whitespace-pre-wrap">{children}</p>
                : <div className="mb-2 whitespace-pre-wrap last:mb-0">{children}</div>
        ),
        table: ({ children }: ComponentPropsWithoutRef<'table'>) => (
            <div
                data-markdown-table-viewport
                className={cn(
                    "custom-scrollbar w-full overflow-auto overscroll-contain rounded-lg border border-border/70 bg-card/60",
                    surface === "document" ? "my-4 max-h-[70vh]" : "my-3 max-h-80",
                )}
            >
                <table className="m-0 w-max min-w-full border-collapse text-left text-xs [&_tbody_tr:last-child_td]:border-b-0">
                    {children}
                </table>
            </div>
        ),
        th: ({ children }: ComponentPropsWithoutRef<'th'>) => (
            <th className="sticky top-0 z-[1] whitespace-nowrap border-b border-r border-border bg-muted px-3 py-2 align-top font-semibold text-foreground last:border-r-0">
                {children}
            </th>
        ),
        td: ({ children }: ComponentPropsWithoutRef<'td'>) => (
            <td className="whitespace-nowrap border-b border-r border-border/60 px-3 py-2 align-top last:border-r-0">
                {children}
            </td>
        ),
        code: CodeBlock,
        a: ({ href, children }: ComponentPropsWithoutRef<'a'>) => {
            if (!href) return <>{children}</>;
            const workspacePath = decodeWorkbenchFileHref(href);
            if (workspacePath) {
                return (
                    <button
                        data-v8-context-open-workbench
                        type="button"
                        onClick={() => void resolveAndOpenWorkspaceFile(workspacePath, { sessionId: sessionId || undefined }).catch((error) => console.warn("[Workbench] Failed to open workspace file", error))}
                        className="font-mono text-primary underline decoration-primary/35 underline-offset-2 hover:decoration-primary focus-visible:ring-2 focus-visible:ring-primary"
                    >
                        {children}
                    </button>
                );
            }
            const resolvedHref = normalizeSurfaceHref(href);
            if (!resolvedHref) return <>{children}</>;

            // 检查是否是视频平台 URL
            for (const platform of VIDEO_PLATFORMS) {
                if (platform.regex.test(resolvedHref)) {
                    return <EmbeddedVideo url={resolvedHref} />;
                }
            }

            // 优先使用增强的 Image 检测
            if (isImageUrl(resolvedHref)) {
                return <ImagePreview src={resolvedHref} alt={String(children)} />;
            }

            const isVideo = MEDIA_EXTENSIONS.video.test(resolvedHref);
            const isAudio = MEDIA_EXTENSIONS.audio.test(resolvedHref);
            // const isImage = matches above

            if (isVideo) {
                return <MediaPlayer src={resolvedHref} type="video" title={String(children)} />;
            }
            if (isAudio) {
                return <MediaPlayer src={resolvedHref} type="audio" title={String(children)} />;
            }

            const documentType = classifyDocumentUrl(resolvedHref);
            if (documentType === "ppt") {
                return <PPTCard url={resolvedHref} filename={decodeFilenameFromUrl(resolvedHref, String(children) || "Presentation.pptx")} />;
            }
            if (documentType === "pdf" || documentType === "model3d") {
                return (
                    <DocumentLinkCard
                        href={resolvedHref}
                        label={String(children)}
                        type={documentType}
                    />
                );
            }

            return (
                <a href={resolvedHref} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline font-medium break-all">
                    {children}
                </a>
            );
        },
        img: ({ src, alt }: ComponentPropsWithoutRef<'img'>) => {
            const resolvedSrc = normalizeSurfaceHref(String(src || ""));
            if (!resolvedSrc) {
                return null;
            }
            return <ImagePreview src={resolvedSrc} alt={alt} />;
        }
    }), [sessionId, surface]);

    return (
        <motion.div
            layout={surface !== "document"}
            className={cn(
                "prose dark:prose-invert max-w-none text-sm break-words w-full leading-relaxed my-1",
                surface === "document" ? "prose-sm overflow-visible" : "overflow-visible",
            )}
        >
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={rehypePlugins}
                components={components}
            >
                {processedContent}
            </ReactMarkdown>
        </motion.div>
    );
}, (prev, next) => prev.content === next.content && prev.searchQuery === next.searchQuery && prev.surface === next.surface);

MarkdownRenderer.displayName = 'MarkdownRenderer';

