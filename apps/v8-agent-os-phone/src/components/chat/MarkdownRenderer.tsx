import { memo, useMemo } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";

import { MediaPlayer, ImagePreview } from "@/src/components/chat/MediaRenderers";
import { normalizeRenderableWorkspaceLinks, mapWindowsWorkspacePathToRenderableLink, normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { spacing } from "@/src/theme/tokens";

type InlineToken =
    | { type: "text"; value: string }
    | { type: "link"; value: string; href: string };

const MARKDOWN_LINK = /\[([^\]]+)\]\(([^)]+)\)/gi;
const BARE_LINK = /(https?:\/\/[^\s)]+|\/(?:api\/workspace\/files\/[^\s)]+|api\/client\/workspace\/files\/[^\s)]+|api\/(?:client\/)?artifacts\/[^/\s)]+\/content(?:\?[^\s)]*)?|v1\/artifacts\/[^/\s)]+\/content(?:\?[^\s)]*)?|workspace\/[^\s)]+))/gi;
const WINDOWS_PATH = /([A-Za-z]:\\[^\s<>"]+)/gi;
const IMAGE_URL = /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i;
const VIDEO_URL = /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i;
const AUDIO_URL = /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i;
const ARTIFACT_CONTENT_URL = /\/(?:v1|api(?:\/client)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;

function isArtifactContentHref(value: string) {
    return ARTIFACT_CONTENT_URL.test(String(value || "").trim());
}

function isRenderableHref(value: string) {
    const href = String(value || "").trim();
    return /^https?:\/\//i.test(href)
        || href.startsWith("/workspace/")
        || href.startsWith("/api/workspace/files/")
        || href.startsWith("/api/client/workspace/files/")
        || href.startsWith("/v1/artifacts/")
        || href.startsWith("/api/artifacts/")
        || href.startsWith("/api/client/artifacts/");
}

function tokenizeInline(content: string, windowsPathLinks: Map<string, string>): InlineToken[] {
    const tokens: InlineToken[] = [];
    let cursor = 0;
    const markdownMatches = Array.from(content.matchAll(MARKDOWN_LINK));

    if (markdownMatches.length === 0) {
        let innerCursor = 0;
        const allMatches = [
            ...Array.from(content.matchAll(BARE_LINK)).map((match) => ({ kind: "url" as const, match })),
            ...Array.from(content.matchAll(WINDOWS_PATH)).map((match) => ({ kind: "path" as const, match })),
        ].sort((left, right) => (left.match.index ?? 0) - (right.match.index ?? 0));

        if (allMatches.length === 0) {
            return [{ type: "text", value: content }];
        }
        for (const entry of allMatches) {
            const match = entry.match;
            const index = match.index ?? 0;
            if (index > innerCursor) {
                tokens.push({ type: "text", value: content.slice(innerCursor, index) });
            }
            if (entry.kind === "url") {
                tokens.push({ type: "link", value: match[0], href: match[0] });
            } else {
                const href = windowsPathLinks.get(match[0]);
                if (href) {
                    tokens.push({ type: "link", value: match[0], href });
                } else {
                    tokens.push({ type: "text", value: match[0] });
                }
            }
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
    const trimmed = line.trim();
    const markdownImage = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    const markdownLink = trimmed.match(/^\[([^\]]*)\]\(([^)]+)\)$/);
    const rawHref = markdownImage?.[2]
        || markdownLink?.[2]
        || (isRenderableHref(trimmed) ? trimmed : null);
    const href = rawHref ? normalizeRenderableWorkspaceUrl(adminBaseUrl, rawHref) : null;
    const label = markdownImage?.[1] || markdownLink?.[1] || undefined;
    if (!href) return null;
    if (markdownImage) return { kind: "image" as const, href, label };
    if (IMAGE_URL.test(href) || isArtifactContentHref(href)) return { kind: "image" as const, href, label };
    if (VIDEO_URL.test(href)) return { kind: "video" as const, href, label };
    if (AUDIO_URL.test(href)) return { kind: "audio" as const, href, label };
    return null;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: { content: string }) {
    const { adminBaseUrl } = useAppSession();
    const { colors } = useUiPrefs();
    const normalizedContent = useMemo(
        () => normalizeRenderableWorkspaceLinks(adminBaseUrl, String(content || "")),
        [adminBaseUrl, content],
    );
    const windowsPathLinks = useMemo(
        () => mapWindowsWorkspacePathToRenderableLink(adminBaseUrl, normalizedContent),
        [adminBaseUrl, normalizedContent],
    );
    const paragraphs = useMemo(
        () => normalizedContent.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean),
        [normalizedContent],
    );

    return (
        <View style={styles.stack}>
            {paragraphs.map((paragraph, index) => {
                const media = resolveMedia(paragraph, adminBaseUrl);
                if (media?.kind === "image") {
                    return <ImagePreview key={`${index}:${media.href}`} src={media.href} alt={media.label} />;
                }
                if (media?.kind === "video" || media?.kind === "audio") {
                    return <MediaPlayer key={`${index}:${media.href}`} src={media.href} type={media.kind} title={media.label} />;
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
                                return <ImagePreview key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} alt={lineMedia.label} />;
                            }
                            if (lineMedia?.kind === "video" || lineMedia?.kind === "audio") {
                                return <MediaPlayer key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} type={lineMedia.kind} title={lineMedia.label} />;
                            }
                            const tokens = tokenizeInline(bulletMatch ? bulletMatch[1] : line, windowsPathLinks);
                            return (
                                <Text
                                    key={`${lineIndex}:${line.slice(0, 12)}`}
                                    selectable
                                    style={[styles.text, { color: colors.textMuted }]}
                                >
                                    {bulletMatch ? <Text style={{ color: colors.text }}>• </Text> : null}
                                    {tokens.map((token, tokenIndex) => {
                                        if (token.type === "link") {
                                            return (
                                                <Text
                                                    key={`${token.href}:${tokenIndex}`}
                                                    selectable
                                                    style={[styles.link, { color: colors.primaryDeep }]}
                                                    onPress={() => void Linking.openURL(normalizeRenderableWorkspaceUrl(adminBaseUrl, token.href))}
                                                >
                                                    {token.value}
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
