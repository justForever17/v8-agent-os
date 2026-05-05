import { memo, useMemo } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";

import { MediaPlayer, ImagePreview } from "@/src/components/chat/MediaRenderers";
import {
    normalizeRenderableWorkspaceUrl,
    resolveRenderableMediaCandidates,
    resolveRenderableMediaUrl,
} from "@/src/lib/workspace-links";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { spacing } from "@/src/theme/tokens";

type InlineToken =
    | { type: "text"; value: string }
    | { type: "link"; value: string; href: string };

const MARKDOWN_LINK = /\[([^\]]+)\]\(([^)]+)\)/gi;
const BARE_LINK = /(https?:\/\/[^\s)'"`]+|\/(?:api\/workspace\/files\/[^\s)'"`]+|api\/client\/workspace\/files\/[^\s)'"`]+|api\/(?:client\/)?workspace\/resource\?[^\s)'"`]+|workspace\/[^\s)'"`]+)|downloaded_media\/[^\s)'"`]+)/gi;
const IMAGE_URL = /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i;
const VIDEO_URL = /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i;
const AUDIO_URL = /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i;
const RAW_VIDEO_TAG = /^<video\b([^>]*)>(?:[\s\S]*?)<\/video>$/i;
const RAW_AUDIO_TAG = /^<audio\b([^>]*)>(?:[\s\S]*?)<\/audio>$/i;
const RAW_IMAGE_TAG = /^<img\b([^>]*)\/?>$/i;

function normalizeMediaTagAttributes(raw: string) {
    return String(raw || "")
        .replace(/\\"/g, "\"")
        .replace(/\\'/g, "'")
        .replace(/&quot;/gi, "\"")
        .replace(/&#39;/gi, "'");
}

function looksLikeWindowsAbsolutePath(value: string) {
    return /^[a-z]:\\/i.test(String(value || "").trim());
}

function looksLikeWorkspaceRelativeMediaPath(value: string) {
    return /^downloaded_media\/.+/i.test(String(value || "").trim());
}

function renderLinkLabel(value: string) {
    const normalized = String(value || "").trim();
    if (!looksLikeWindowsAbsolutePath(normalized)) {
        return normalized;
    }
    const segments = normalized.split(/[\\/]/).filter(Boolean);
    return segments[segments.length - 1] || normalized;
}

function isRenderableHref(value: string) {
    const href = String(value || "").trim();
    return /^https?:\/\//i.test(href)
        || looksLikeWindowsAbsolutePath(href)
        || looksLikeWorkspaceRelativeMediaPath(href)
        || href.startsWith("/workspace/")
        || href.startsWith("/api/workspace/files/")
        || href.startsWith("/api/client/workspace/files/")
        || href.startsWith("/api/workspace/resource?")
        || href.startsWith("/api/client/workspace/resource?");
}

function unwrapInlineCodeToken(value: string) {
    const normalized = String(value || "").trim();
    const match = normalized.match(/^`([^`]+)`$/);
    return match?.[1]?.trim() || normalized;
}

function cleanHrefCandidate(value: string) {
    return String(value || "")
        .trim()
        .replace(/^[`'"“”‘’]+/, "")
        .replace(/[\uFF0C\u3002\uFF1B\u3001,.;:!?\uFF01\uFF1F`'"\u201C\u201D\u2018\u2019]+$/g, "")
        .trim();
}

function inferMediaKindFromCandidate(value: string) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return null;
    }
    if (IMAGE_URL.test(normalized)) return "image" as const;
    if (VIDEO_URL.test(normalized)) return "video" as const;
    if (AUDIO_URL.test(normalized)) return "audio" as const;
    return null;
}

function findEmbeddedMediaHref(value: string) {
    const normalized = String(value || "");
    const markdownImage = normalized.match(/!\[([^\]]*)\]\(([^)]+)\)/);
    const markdownLink = normalized.match(/\[([^\]]*)\]\(([^)]+)\)/);
    const markdownHref = markdownImage?.[2] || markdownLink?.[2] || "";
    if (markdownHref) {
        const href = cleanHrefCandidate(markdownHref);
        if (isRenderableHref(href) && inferMediaKindFromCandidate(href)) {
            return { href, label: markdownImage?.[1] || markdownLink?.[1] || undefined };
        }
    }
    for (const match of normalized.matchAll(BARE_LINK)) {
        const href = cleanHrefCandidate(match[0]);
        if (isRenderableHref(href) && inferMediaKindFromCandidate(href)) {
            return { href, label: undefined };
        }
    }
    return null;
}

function tokenizeInline(content: string): InlineToken[] {
    const tokens: InlineToken[] = [];
    let cursor = 0;
    const markdownMatches = Array.from(content.matchAll(MARKDOWN_LINK));

    if (markdownMatches.length === 0) {
        let innerCursor = 0;
        const allMatches = Array.from(content.matchAll(BARE_LINK)).map((match) => ({ kind: "url" as const, match }));

        if (allMatches.length === 0) {
            return [{ type: "text", value: content }];
        }
        for (const entry of allMatches) {
            const match = entry.match;
            const index = match.index ?? 0;
            if (index > innerCursor) {
                tokens.push({ type: "text", value: content.slice(innerCursor, index) });
            }
            tokens.push({ type: "link", value: match[0], href: match[0] });
            innerCursor = index + match[0].length;
        }
        if (innerCursor < content.length) {
            tokens.push({ type: "text", value: content.slice(innerCursor) });
        }
        return tokens;
    }

    for (const match of markdownMatches) {
        const index = match.index ?? 0;
        if (index > cursor) {
            tokens.push({ type: "text", value: content.slice(cursor, index) });
        }
        if (isRenderableHref(match[2])) {
            tokens.push({ type: "link", value: match[1], href: match[2] });
        } else {
            tokens.push({ type: "text", value: match[0] });
        }
        cursor = index + match[0].length;
    }
    if (cursor < content.length) {
        tokens.push({ type: "text", value: content.slice(cursor) });
    }
    return tokens;
}

function resolveMedia(line: string, adminBaseUrl: string) {
    const trimmed = normalizeMediaTagAttributes(line.trim());
    const normalizedLine = unwrapInlineCodeToken(trimmed);
    const rawMediaMatch = trimmed.match(RAW_VIDEO_TAG) || trimmed.match(RAW_AUDIO_TAG) || trimmed.match(RAW_IMAGE_TAG);
    if (rawMediaMatch) {
        const attrs = rawMediaMatch[1] || "";
        const rawHref = attrs.match(/\ssrc=(["'])(.*?)\1/i)?.[2]
            || attrs.match(/\ssrc=([^\s>]+)/i)?.[1]
            || "";
        const candidates = rawHref ? resolveRenderableMediaCandidates(adminBaseUrl, rawHref) : [];
        const href = candidates[0] || null;
        const label = attrs.match(/\stitle=(["'])(.*?)\1/i)?.[2]
            || attrs.match(/\salt=(["'])(.*?)\1/i)?.[2]
            || undefined;
        if (!href) {
            return null;
        }
        if (RAW_VIDEO_TAG.test(trimmed)) {
            return { kind: "video" as const, href, label, candidates };
        }
        if (RAW_AUDIO_TAG.test(trimmed)) {
            return { kind: "audio" as const, href, label, candidates };
        }
        return { kind: "image" as const, href, label, candidates };
    }

    const markdownImage = normalizedLine.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    const markdownLink = normalizedLine.match(/^\[([^\]]*)\]\(([^)]+)\)$/);
    const embeddedMedia = findEmbeddedMediaHref(normalizedLine);
    const rawHref = markdownImage?.[2]
        || markdownLink?.[2]
        || (isRenderableHref(normalizedLine) ? normalizedLine : null)
        || embeddedMedia?.href
        || null;
    const candidates = rawHref ? resolveRenderableMediaCandidates(adminBaseUrl, rawHref) : [];
    const href = candidates[0] || null;
    const label = markdownImage?.[1] || markdownLink?.[1] || embeddedMedia?.label || undefined;
    if (!href) return null;
    if (markdownImage) return { kind: "image" as const, href, label, candidates };
    const inferredKind = inferMediaKindFromCandidate(rawHref || href);
    if (inferredKind === "image") return { kind: "image" as const, href, label, candidates };
    if (inferredKind === "video") return { kind: "video" as const, href, label, candidates };
    if (inferredKind === "audio") return { kind: "audio" as const, href, label, candidates };
    return null;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: { content: string }) {
    const { adminBaseUrl } = useAppSession();
    const { colors } = useUiPrefs();
    const paragraphs = useMemo(
        () => String(content || "").split(/\n{2,}/).map((item) => item.trim()).filter(Boolean),
        [content],
    );

    return (
        <View style={styles.stack}>
            {paragraphs.map((paragraph, index) => {
                const media = resolveMedia(paragraph, adminBaseUrl);
                if (media?.kind === "image") {
                    return <ImagePreview key={`${index}:${media.href}`} src={media.href} alt={media.label} candidates={media.candidates} />;
                }
                if (media?.kind === "video" || media?.kind === "audio") {
                    return <MediaPlayer key={`${index}:${media.href}`} src={media.href} type={media.kind} title={media.label} candidates={media.candidates} />;
                }

                const lines = paragraph.split("\n");
                return (
                    <View key={`${index}:${paragraph.slice(0, 24)}`} style={styles.paragraph}>
                        {lines.map((line, lineIndex) => {
                            const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
                            if (headingMatch) {
                                const level = headingMatch[1].length;
                                return (
                                    <Text
                                        key={`${lineIndex}:${line.slice(0, 12)}`}
                                        selectable
                                        style={[
                                            level === 1 ? styles.h1 : level === 2 ? styles.h2 : styles.h3,
                                            { color: colors.text },
                                        ]}
                                    >
                                        {headingMatch[2]}
                                    </Text>
                                );
                            }

                            const bulletMatch = line.match(/^[-*]\s+(.+)$/);
                            const lineMedia = resolveMedia(bulletMatch ? bulletMatch[1] : line, adminBaseUrl);
                            if (lineMedia?.kind === "image") {
                                return <ImagePreview key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} alt={lineMedia.label} candidates={lineMedia.candidates} />;
                            }
                            if (lineMedia?.kind === "video" || lineMedia?.kind === "audio") {
                                return <MediaPlayer key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} type={lineMedia.kind} title={lineMedia.label} candidates={lineMedia.candidates} />;
                            }
                            const tokens = tokenizeInline(bulletMatch ? bulletMatch[1] : line);
                            return (
                                <Text
                                    key={`${lineIndex}:${line.slice(0, 12)}`}
                                    selectable
                                    style={[styles.text, { color: colors.text }]}
                                >
                                    {bulletMatch ? <Text style={{ color: colors.text }}>• </Text> : null}
                                    {tokens.map((token, tokenIndex) => {
                                        if (token.type === "link") {
                                            const label = renderLinkLabel(token.value);
                                            if (looksLikeWindowsAbsolutePath(token.value)) {
                                                return (
                                                    <Text
                                                        key={`${token.href}:${tokenIndex}`}
                                                        style={{ color: colors.text }}
                                                    >
                                                        {label}
                                                    </Text>
                                                );
                                            }
                                            return (
                                                <Text
                                                    key={`${token.href}:${tokenIndex}`}
                                                    selectable
                                                    style={[styles.link, { color: colors.primaryDeep }]}
                                                    onPress={() => void Linking.openURL(normalizeRenderableWorkspaceUrl(adminBaseUrl, token.href))}
                                                >
                                                    {label}
                                                </Text>
                                            );
                                        }
                                        return <Text key={`${tokenIndex}:${token.value.slice(0, 12)}`}>{token.value}</Text>;
                                    })}
                                </Text>
                            );
                        })}
                    </View>
                );
            })}
        </View>
    );
});

const styles = StyleSheet.create({
    stack: {
        gap: spacing.sm,
        width: "100%",
    },
    paragraph: {
        gap: spacing.xs,
    },
    text: {
        fontSize: 14,
        lineHeight: 21,
    },
    link: {
        textDecorationLine: "underline",
    },
    h1: {
        fontSize: 22,
        lineHeight: 28,
        fontWeight: "800",
    },
    h2: {
        fontSize: 18,
        lineHeight: 24,
        fontWeight: "800",
    },
    h3: {
        fontSize: 16,
        lineHeight: 22,
        fontWeight: "700",
    },
});
