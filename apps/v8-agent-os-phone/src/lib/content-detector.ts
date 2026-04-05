export type PhoneContentBlockType =
    | "thinking"
    | "tool"
    | "artifact"
    | "voice"
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
                blocks.push({
                    id: `text-${blockIndex.current++}`,
                    type: "text",
                    content: before,
                });
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
            blocks.push({
                id: `text-${blockIndex.current++}`,
                type: rest.includes("```") ? "markdown" : "text",
                content: rest,
            });
        }
    }
}

function buildArtifactData(attrs: string) {
    const titleMatch = attrs.match(/title="([^"]+)"/i);
    const typeMatch = attrs.match(/type="([^"]+)"/i);
    return {
        title: titleMatch ? titleMatch[1] : undefined,
        type: typeMatch ? typeMatch[1] : undefined,
    };
}

export function parsePhoneContentBlocks(content: string, isStreaming = false, startId = 0): PhoneContentBlock[] {
    const blocks: PhoneContentBlock[] = [];
    const blockIndex = { current: startId };
    let processedContent = String(content || "");

    if (!processedContent.trim()) {
        return blocks;
    }

    if (isStreaming) {
        const incompleteTagMatch = processedContent.match(/<(?:t|th|thi|thin|think|to|too|tool|a|ar|art|arti|artif|artifa|artifac|artifact|v|vo|voi|voic|voice|\/|\/t|\/th|\/thi|\/thin|\/think|\/a|\/ar|\/art|\/arti|\/artif|\/artifac|\/artifact|\/v|\/vo|\/voi|\/voic|\/voice)[^>]*$/i);
        const incompleteAttrMatch = processedContent.match(/<(?:tool-call|artifact|voice)[^>]+$/i);
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
            blocks.push({
                id: `artifact-${blockIndex.current++}`,
                type: "artifact",
                content: String(match[6] || "").trim(),
                data: buildArtifactData(String(match[5] || "")),
            });
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
