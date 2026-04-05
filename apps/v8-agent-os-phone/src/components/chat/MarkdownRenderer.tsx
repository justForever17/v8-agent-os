import { memo, useMemo } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";

import { MediaPlayer, ImagePreview } from "@/src/components/chat/MediaRenderers";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { spacing } from "@/src/theme/tokens";

type InlineToken =
    | { type: "text"; value: string }
    | { type: "link"; value: string; href: string };

const MARKDOWN_LINK = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/gi;
const BARE_LINK = /(https?:\/\/[^\s)]+)/gi;
const IMAGE_URL = /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i;
const VIDEO_URL = /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i;
const AUDIO_URL = /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i;

function tokenizeInline(content: string): InlineToken[] {
    const tokens: InlineToken[] = [];
    let cursor = 0;
    const markdownMatches = Array.from(content.matchAll(MARKDOWN_LINK));

    if (markdownMatches.length === 0) {
        let innerCursor = 0;
        const bareMatches = Array.from(content.matchAll(BARE_LINK));
        if (bareMatches.length === 0) {
            return [{ type: "text", value: content }];
        }
        for (const match of bareMatches) {
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
        tokens.push({ type: "link", value: match[1], href: match[2] });
        cursor = index + match[0].length;
    }
    if (cursor < content.length) {
        tokens.push({ type: "text", value: content.slice(cursor) });
    }
    return tokens;
}

function resolveMedia(line: string) {
    const trimmed = line.trim();
    const markdown = trimmed.match(/^\[([^\]]*)\]\((https?:\/\/[^)]+)\)$/);
    const href = markdown?.[2] || (trimmed.match(/^https?:\/\/\S+$/)?.[0] ?? null);
    const label = markdown?.[1] || undefined;
    if (!href) return null;
    if (IMAGE_URL.test(href)) return { kind: "image" as const, href, label };
    if (VIDEO_URL.test(href)) return { kind: "video" as const, href, label };
    if (AUDIO_URL.test(href)) return { kind: "audio" as const, href, label };
    return null;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: { content: string }) {
    const { colors } = useUiPrefs();
    const paragraphs = useMemo(
        () => String(content || "").split(/\n{2,}/).map((item) => item.trim()).filter(Boolean),
        [content],
    );

    return (
        <View style={styles.stack}>
            {paragraphs.map((paragraph, index) => {
                const media = resolveMedia(paragraph);
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
                            const tokens = tokenizeInline(bulletMatch ? bulletMatch[1] : line);
                            return (
                                <Text key={`${lineIndex}:${line.slice(0, 12)}`} style={[styles.text, { color: colors.textMuted }]}>
                                    {bulletMatch ? <Text style={{ color: colors.text }}>• </Text> : null}
                                    {tokens.map((token, tokenIndex) => {
                                        if (token.type === "link") {
                                            return (
                                                <Text
                                                    key={`${token.href}:${tokenIndex}`}
                                                    style={[styles.link, { color: colors.primaryDeep }]}
                                                    onPress={() => void Linking.openURL(token.href)}
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
