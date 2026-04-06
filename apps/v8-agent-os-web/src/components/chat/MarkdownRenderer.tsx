import { memo, useEffect, useMemo, useState, ComponentPropsWithoutRef } from 'react';
import { cn } from "@/lib/utils";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { motion } from 'framer-motion';
import { coerceAdminResourceRef, resolveAdminResourceUrl } from "@v8/session-realtime";
import { MediaPlayer, ImagePreview } from './MediaRenderers';
import { CodeBlock as SharedCodeBlock } from './CodeBlock';
import { useDebouncedValue } from '@/hooks/use-debounce';

// 宽松的媒体文件扩展名检测 (允许 URL 后带有参数, 如 ?token=...)
const MEDIA_EXTENSIONS = {
    video: /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i,
    audio: /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i,
    image: /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i,
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
    // 0. 将本地工作区路径自动转换为引擎的 Web 访问 URL
    // 支持匹配: E:\...\workspace\file.png 或 /usr/...\workspace\file.png 或单纯的 workspace/file.png
    let processed = content.replace(
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
}

export const MarkdownRenderer = memo(({ content }: MarkdownRendererProps) => {
    // 防抖 10ms 避免高频流式更新
    const debouncedContent = useDebouncedValue(content, 10);
    const [workspaceAssetBaseUrl, setWorkspaceAssetBaseUrl] = useState(() => cachedWorkspaceAssetBaseUrl);

    useEffect(() => {
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
    }, [workspaceAssetBaseUrl]);

    // 预处理：将纯 URL 转换为 Markdown 链接
    const processedContent = useMemo(
        () => preprocessContent(debouncedContent, workspaceAssetBaseUrl),
        [debouncedContent, workspaceAssetBaseUrl]
    );

    const components = useMemo(() => ({
        pre: ({ children }: ComponentPropsWithoutRef<'pre'>) => <>{children}</>,
        p: ({ children }: ComponentPropsWithoutRef<'p'>) => (
            <div className="mb-2 last:mb-0">{children}</div>
        ),
        code: CodeBlock,
        a: ({ href, children }: ComponentPropsWithoutRef<'a'>) => {
            if (!href) return <>{children}</>;
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
    }), []);

    return (
        <motion.div layout className="prose dark:prose-invert max-w-none text-sm break-words w-full overflow-x-auto leading-relaxed my-1">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={components}
            >
                {processedContent}
            </ReactMarkdown>
        </motion.div>
    );
}, (prev, next) => prev.content === next.content);

MarkdownRenderer.displayName = 'MarkdownRenderer';

