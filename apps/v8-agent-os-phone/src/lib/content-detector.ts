export type PhoneContentBlockType =
    | "thinking"
    | "tool"
    | "voice"
    | "image"
    | "video"
    | "audio"
    | "code"
    | "mermaid"
    | "html_snippet"
    | "model-3d"
    | "file-ppt"
    | "file-html"
    | "text"
    | "markdown";

export type PhoneContentBlock = {
    id: string;
    type: PhoneContentBlockType;
    content: string;
    isStreaming?: boolean;
    data?: Record<string, unknown>;
};

function normalizeMediaTagAttributes(raw: string) {
    return String(raw || "")
        .replace(/\\"/g, "\"")
        .replace(/\\'/g, "'")
        .replace(/&quot;/gi, "\"")
        .replace(/&#39;/gi, "'");
}

const INLINE_MEDIA_PATH_REGEX = /`((?:downloaded_media\/[^\s`]+)\.(?:jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff|mp4|webm|mov|avi|mkv|mp3|wav|ogg|m4a|flac|aac)(?:\?[^\s`]+)?)`|((?:downloaded_media\/[^\s"'<>]+)\.(?:jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff|mp4|webm|mov|avi|mkv|mp3|wav|ogg|m4a|flac|aac)(?:\?[^\s"'<>]+)?)/gi;

function inferInlineMediaPathType(raw: string): "image" | "video" | "audio" | null {
    const value = String(raw || "").trim();
    if (!value) {
        return null;
    }
    if (/\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i.test(value)) {
        return "image";
    }
    if (/\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i.test(value)) {
        return "video";
    }
    if (/\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i.test(value)) {
        return "audio";
    }
    return null;
}

function pushInlineMediaPathBlocks(blocks: PhoneContentBlock[], content: string, blockIndex: { current: number }) {
    const text = String(content || "");
    if (!text) {
        return;
    }

    INLINE_MEDIA_PATH_REGEX.lastIndex = 0;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = INLINE_MEDIA_PATH_REGEX.exec(text)) !== null) {
        if (match.index > lastIndex) {
            pushPlainTextBlock(blocks, text.slice(lastIndex, match.index), blockIndex);
        }

        const rawPath = String(match[1] || match[2] || "").trim();
        const mediaType = inferInlineMediaPathType(rawPath);
        if (rawPath && mediaType) {
            blocks.push({
                id: `${mediaType}-${blockIndex.current++}`,
                type: mediaType,
                content: rawPath,
                data: {
                    src: rawPath,
                    title: rawPath.split(/[\\/]/).filter(Boolean).pop() || rawPath,
                },
            });
        } else {
            pushPlainTextBlock(blocks, match[0], blockIndex);
        }

        lastIndex = INLINE_MEDIA_PATH_REGEX.lastIndex;
    }

    if (lastIndex < text.length) {
        pushPlainTextBlock(blocks, text.slice(lastIndex), blockIndex);
    }
}

function pushPlainTextBlock(blocks: PhoneContentBlock[], content: string, blockIndex: { current: number }) {
    const text = String(content || "");
    if (!text) {
        return;
    }

    blocks.push({
        id: `text-${blockIndex.current++}`,
        type: text.includes("```") ? "markdown" : "text",
        content: text,
    });
}

function parseInlineMediaTag(raw: string) {
    const trimmed = normalizeMediaTagAttributes(String(raw || "").trim());
    if (!trimmed) {
        return null;
    }

    const pairedMatch = trimmed.match(/^<(video|audio)\b([^>]*)>(?:[\s\S]*?)<\/\1>$/i);
    const imageMatch = trimmed.match(/^<img\b([^>]*)\/?>$/i);
    const type = pairedMatch?.[1]?.toLowerCase() || (imageMatch ? "image" : "");
    const attrs = pairedMatch?.[2] || imageMatch?.[1] || "";
    if (!type || !attrs) {
        return null;
    }

    const src = attrs.match(/\ssrc=(["'])(.*?)\1/i)?.[2]
        || attrs.match(/\ssrc=([^\s>]+)/i)?.[1]
        || "";
    if (!src.trim()) {
        return null;
    }

    const title = attrs.match(/\stitle=(["'])(.*?)\1/i)?.[2]
        || attrs.match(/\salt=(["'])(.*?)\1/i)?.[2]
        || "";

    return {
        type: type as "image" | "video" | "audio",
        src: src.trim(),
        title: title.trim() || undefined,
    };
}

function pushRenderableTextBlocks(blocks: PhoneContentBlock[], content: string, blockIndex: { current: number }) {
    const text = String(content || "");
    if (!text) {
        return;
    }

    const mediaTagRegex = /<(video|audio)\b[^>]*>[\s\S]*?<\/\1>|<img\b[^>]*\/?>/gi;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = mediaTagRegex.exec(text)) !== null) {
        if (match.index > lastIndex) {
            pushInlineMediaPathBlocks(blocks, text.slice(lastIndex, match.index), blockIndex);
        }

        const media = parseInlineMediaTag(match[0]);
        if (media) {
            blocks.push({
                id: `${media.type}-${blockIndex.current++}`,
                type: media.type,
                content: media.src,
                data: {
                    src: media.src,
                    title: media.title,
                },
            });
        } else {
            pushPlainTextBlock(blocks, match[0], blockIndex);
        }

        lastIndex = mediaTagRegex.lastIndex;
    }

    if (lastIndex < text.length) {
        pushInlineMediaPathBlocks(blocks, text.slice(lastIndex), blockIndex);
    }
}

function pushTextBlock(blocks: PhoneContentBlock[], content: string, blockIndex: { current: number }) {
    const text = String(content || "");
    if (!text) {
        return;
    }

    const codeBlockRegex = /```(\w*)(?:\n| )([\s\S]*?)(?:```|$)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = codeBlockRegex.exec(text)) !== null) {
        if (match.index > lastIndex) {
            const before = text.slice(lastIndex, match.index);
            if (before) {
                pushRenderableTextBlocks(blocks, before, blockIndex);
            }
        }

        const language = String(match[1] || "text").trim().toLowerCase();
        const codeContent = String(match[2] || "");
        const type: PhoneContentBlockType = language === "mermaid"
            ? "mermaid"
            : language === "html" || language === "xml"
                ? "html_snippet"
                : "code";

        blocks.push({
            id: `${type}-${blockIndex.current++}`,
            type,
            content: codeContent,
            data: language ? { language } : undefined,
        });

        lastIndex = codeBlockRegex.lastIndex;
    }

    if (lastIndex < text.length) {
        const rest = text.slice(lastIndex);
        if (rest) {
            pushRenderableTextBlocks(blocks, rest, blockIndex);
        }
    }
}

export function parsePhoneContentBlocks(content: string, isStreaming = false, startId = 0): PhoneContentBlock[] {
    const blocks: PhoneContentBlock[] = [];
    const blockIndex = { current: startId };
    let processedContent = String(content || "");

    if (!processedContent.trim()) {
        return blocks;
    }

    if (isStreaming) {
        const incompleteTagMatch = processedContent.match(/<(?:t|th|thi|thin|think|to|too|tool|a|ar|art|arti|artif|artifa|artifac|artifact|v|vo|voi|voic|voice|vid|vide|video|au|aud|audi|audio|im|img|\/|\/t|\/th|\/thi|\/thin|\/think|\/a|\/ar|\/art|\/arti|\/artif|\/artifac|\/artifact|\/v|\/vo|\/voi|\/voic|\/voice|\/vid|\/vide|\/video|\/au|\/aud|\/audi|\/audio)[^>]*$/i);
        const incompleteAttrMatch = processedContent.match(/<(?:tool-call|artifact|voice|video|audio|img)[^>]+$/i);
        if (incompleteTagMatch) {
            processedContent = processedContent.slice(0, incompleteTagMatch.index);
        } else if (incompleteAttrMatch && !incompleteAttrMatch[0].endsWith(">")) {
            processedContent = processedContent.slice(0, incompleteAttrMatch.index);
        }
    }

    const splitRegex = /(<think(?:>|\s+[^>]*>))([\s\S]*?)(?:<\/think>|$)|(<tool-call id="([^"]+)"\s*\/>)|(<artifact\s+(?:[^>]*?)>)([\s\S]*?)(?:<\/artifact>|$)|(<voice(?:>|\s+[^>]*>))([\s\S]*?)(?:<\/voice>|$)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = splitRegex.exec(processedContent)) !== null) {
        if (match.index > lastIndex) {
            pushTextBlock(blocks, processedContent.slice(lastIndex, match.index), blockIndex);
        }

        if (match[1]) {
            const openingTag = match[1];
            const thinkContent = String(match[2] || "");
            const timeMatch = openingTag.match(/time="(\d+)"/);
            blocks.push({
                id: `thinking-${blockIndex.current++}`,
                type: "thinking",
                content: thinkContent,
                isStreaming: isStreaming && !match[0].endsWith("</think>"),
                data: timeMatch ? { elapsedTime: Number(timeMatch[1]) || undefined } : undefined,
            });
        } else if (match[3]) {
            blocks.push({
                id: `tool-${blockIndex.current++}`,
                type: "tool",
                content: "Tool Call",
                data: { toolCallId: match[4] },
            });
        } else if (match[5]) {
            const artifactContent = String(match[6] || "").trim();
            if (artifactContent) {
                pushTextBlock(blocks, artifactContent, blockIndex);
            }
        } else if (match[7]) {
            blocks.push({
                id: `voice-${blockIndex.current++}`,
                type: "voice",
                content: String(match[8] || "").trim(),
                isStreaming: isStreaming && !match[0].endsWith("</voice>"),
            });
        }

        lastIndex = splitRegex.lastIndex;
    }

    if (lastIndex < processedContent.length) {
        pushTextBlock(blocks, processedContent.slice(lastIndex), blockIndex);
    }

    return blocks.filter((block) => String(block.content || "").trim());
}

export function hasCompleteVoiceBlock(content: string) {
    return /<voice(?:>|\s+[^>]*>)[\s\S]*?<\/voice>/i.test(String(content || ""));
}

export function hashContentFragment(value: string) {
    let hash = 0;
    for (let index = 0; index < value.length; index += 1) {
        hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
    }
    return Math.abs(hash).toString(36);
}

export function buildVoicePlaybackKey(messageIdentity: string, blockIndex: string, content: string) {
    return `${messageIdentity}:voice:${blockIndex}:${hashContentFragment(content)}`;
}

export function extractVoiceBlockText(content: string) {
    return parsePhoneContentBlocks(content)
        .filter((block) => block.type === "voice")
        .map((block) => block.content.trim())
        .filter(Boolean)
        .join("\n\n")
        .trim();
}

export function stripVoiceBlocks(content: string) {
    return parsePhoneContentBlocks(content)
        .filter((block) => block.type !== "voice")
        .map((block) => block.content)
        .join("\n\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
}
