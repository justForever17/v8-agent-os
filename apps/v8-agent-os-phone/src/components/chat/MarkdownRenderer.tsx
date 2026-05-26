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
    | { type: "link"; value: string; href: string }
    | { type: "code"; value: string }
    | { type: "strong"; value: string }
    | { type: "em"; value: string }
    | { type: "strike"; value: string };

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

type InlineCandidate = {
    type: Exclude<InlineToken["type"], "text">;
    index: number;
    raw: string;
    value: string;
    href?: string;
};

function findRegexCandidate(
    content: string,
    from: number,
    pattern: RegExp,
    build: (match: RegExpExecArray) => InlineCandidate | null,
) {
    pattern.lastIndex = from;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(content)) !== null) {
        const candidate = build(match);
        if (candidate) {
            return candidate;
        }
        if (pattern.lastIndex <= (match.index ?? from)) {
            pattern.lastIndex = (match.index ?? from) + 1;
        }
    }
    return null;
}

function findNextInlineCandidate(content: string, from: number): InlineCandidate | null {
    const candidates = [
        findRegexCandidate(content, from, /\[([^\]]+)\]\(([^)]+)\)/g, (match) => {
            const index = match.index ?? 0;
            if (content[index - 1] === "!") {
                return null;
            }
            const href = cleanHrefCandidate(match[2]);
            return isRenderableHref(href)
                ? { type: "link", index, raw: match[0], value: match[1], href }
                : null;
        }),
        findRegexCandidate(content, from, /`([^`\n]+)`/g, (match) => ({
            type: "code",
            index: match.index ?? 0,
            raw: match[0],
            value: match[1],
        })),
        findRegexCandidate(content, from, /\*\*([^\n]+?)\*\*/g, (match) => ({
            type: "strong",
            index: match.index ?? 0,
            raw: match[0],
            value: match[1],
        })),
        findRegexCandidate(content, from, /__([^\n]+?)__/g, (match) => ({
            type: "strong",
            index: match.index ?? 0,
            raw: match[0],
            value: match[1],
        })),
        findRegexCandidate(content, from, /~~([^\n]+?)~~/g, (match) => ({
            type: "strike",
            index: match.index ?? 0,
            raw: match[0],
            value: match[1],
        })),
        findRegexCandidate(content, from, /(^|[\s([{])\*([^*\n]+?)\*(?=$|[\s.,!?;:)\]}])/g, (match) => ({
            type: "em",
            index: (match.index ?? 0) + match[1].length,
            raw: match[0].slice(match[1].length),
            value: match[2],
        })),
        findRegexCandidate(content, from, /(https?:\/\/[^\s)'"`]+|\/(?:api\/workspace\/files\/[^\s)'"`]+|api\/client\/workspace\/files\/[^\s)'"`]+|api\/(?:client\/)?workspace\/resource\?[^\s)'"`]+|workspace\/[^\s)'"`]+)|downloaded_media\/[^\s)'"`]+)/g, (match) => {
            const href = cleanHrefCandidate(match[0]);
            return href
                ? { type: "link", index: match.index ?? 0, raw: match[0], value: href, href }
                : null;
        }),
    ].filter(Boolean) as InlineCandidate[];

    if (candidates.length === 0) {
        return null;
    }
    return candidates.sort((left, right) => left.index - right.index || left.raw.length - right.raw.length)[0];
}

function tokenizeInline(content: string): InlineToken[] {
    const tokens: InlineToken[] = [];
    let cursor = 0;
    const source = String(content || "");

    while (cursor < source.length) {
        const candidate = findNextInlineCandidate(source, cursor);
        if (!candidate) {
            tokens.push({ type: "text", value: source.slice(cursor) });
            break;
        }
        if (candidate.index > cursor) {
            tokens.push({ type: "text", value: source.slice(cursor, candidate.index) });
        }
        if (candidate.type === "link") {
            tokens.push({ type: "link", value: candidate.value, href: candidate.href || candidate.value });
        } else {
            tokens.push({ type: candidate.type, value: candidate.value });
        }
        cursor = candidate.index + candidate.raw.length;
    }

    return tokens.length ? tokens : [{ type: "text", value: source }];
}

function parseLinePrefix(line: string) {
    const trimmed = String(line || "");
    const unordered = trimmed.match(/^(\s*)[-*+]\s+(?:\[([ xX])\]\s+)?(.+)$/);
    if (unordered) {
        const checked = unordered[2] ? unordered[2].toLowerCase() === "x" : undefined;
        return {
            type: "list" as const,
            prefix: checked === undefined ? "•" : checked ? "☑" : "☐",
            content: unordered[3],
        };
    }
    const ordered = trimmed.match(/^\s*(\d+)[.)]\s+(.+)$/);
    if (ordered) {
        return {
            type: "list" as const,
            prefix: `${ordered[1]}.`,
            content: ordered[2],
        };
    }
    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
        return {
            type: "quote" as const,
            content: quote[1],
        };
    }
    const hr = trimmed.trim().match(/^(-{3,}|\*{3,}|_{3,})$/);
    if (hr) {
        return { type: "hr" as const };
    }
    return { type: "text" as const, content: trimmed };
}

function splitTableCells(line: string) {
    return String(line || "")
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim());
}

function isTableSeparator(line: string) {
    const cells = splitTableCells(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function parseMarkdownTable(lines: string[]) {
    if (lines.length < 2 || !lines[0].includes("|") || !isTableSeparator(lines[1])) {
        return null;
    }
    const headers = splitTableCells(lines[0]);
    const rows = lines.slice(2)
        .filter((line) => line.includes("|"))
        .map((line) => splitTableCells(line));
    if (!headers.length || !rows.length) {
        return null;
    }
    return { headers, rows };
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

    const renderInlineTokens = (tokens: InlineToken[]) => tokens.map((token, tokenIndex) => {
        if (token.type === "link") {
            const label = renderLinkLabel(token.value);
            if (looksLikeWindowsAbsolutePath(token.value)) {
                return (
                    <Text
                        key={`link-local:${tokenIndex}`}
                        style={{ color: colors.text }}
                    >
                        {label}
                    </Text>
                );
            }
            return (
                <Text
                    key={`link:${token.href}:${tokenIndex}`}
                    selectable
                    style={[styles.link, { color: colors.primaryDeep }]}
                    onPress={() => void Linking.openURL(normalizeRenderableWorkspaceUrl(adminBaseUrl, token.href))}
                >
                    {label}
                </Text>
            );
        }
        if (token.type === "strong") {
            return <Text key={`strong:${tokenIndex}`} style={styles.strong}>{token.value}</Text>;
        }
        if (token.type === "em") {
            return <Text key={`em:${tokenIndex}`} style={styles.em}>{token.value}</Text>;
        }
        if (token.type === "strike") {
            return <Text key={`strike:${tokenIndex}`} style={styles.strike}>{token.value}</Text>;
        }
        if (token.type === "code") {
            const href = cleanHrefCandidate(token.value);
            if (isRenderableHref(href) && !looksLikeWindowsAbsolutePath(href)) {
                return (
                    <Text
                        key={`code-link:${href}:${tokenIndex}`}
                        selectable
                        style={[
                            styles.inlineCode,
                            styles.link,
                            {
                                color: colors.primaryDeep,
                                backgroundColor: colors.surface,
                                borderColor: colors.border,
                            },
                        ]}
                        onPress={() => void Linking.openURL(normalizeRenderableWorkspaceUrl(adminBaseUrl, href))}
                    >
                        {renderLinkLabel(href)}
                    </Text>
                );
            }
            return (
                <Text
                    key={`code:${tokenIndex}`}
                    style={[
                        styles.inlineCode,
                        {
                            color: colors.text,
                            backgroundColor: colors.surface,
                            borderColor: colors.border,
                        },
                    ]}
                >
                    {token.value}
                </Text>
            );
        }
        return <Text key={`text:${tokenIndex}`}>{token.value}</Text>;
    });

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
                const table = parseMarkdownTable(lines);
                if (table) {
                    return (
                        <View
                            key={`${index}:table:${paragraph.slice(0, 16)}`}
                            style={[styles.table, { borderColor: colors.border }]}
                        >
                            <View style={[styles.tableRow, styles.tableHeaderRow, { borderBottomColor: colors.border, backgroundColor: colors.surface }]}>
                                {table.headers.map((header, cellIndex) => (
                                    <Text
                                        key={`header:${cellIndex}`}
                                        selectable
                                        style={[styles.tableHeaderCell, { color: colors.text }]}
                                    >
                                        {renderInlineTokens(tokenizeInline(header))}
                                    </Text>
                                ))}
                            </View>
                            {table.rows.map((row, rowIndex) => (
                                <View
                                    key={`row:${rowIndex}`}
                                    style={[
                                        styles.tableRow,
                                        rowIndex < table.rows.length - 1 ? { borderBottomColor: colors.border } : null,
                                    ]}
                                >
                                    {table.headers.map((_, cellIndex) => (
                                        <Text
                                            key={`cell:${rowIndex}:${cellIndex}`}
                                            selectable
                                            style={[styles.tableCell, { color: colors.text }]}
                                        >
                                            {renderInlineTokens(tokenizeInline(row[cellIndex] || ""))}
                                        </Text>
                                    ))}
                                </View>
                            ))}
                        </View>
                    );
                }
                return (
                    <View key={`${index}:${paragraph.slice(0, 24)}`} style={styles.paragraph}>
                        {lines.map((line, lineIndex) => {
                            const lineShape = parseLinePrefix(line);
                            if (lineShape.type === "hr") {
                                return (
                                    <View
                                        key={`${lineIndex}:hr`}
                                        style={[styles.hr, { backgroundColor: colors.border }]}
                                    />
                                );
                            }
                            if (lineShape.type === "quote") {
                                const quoteContent = lineShape.content || "";
                                return (
                                    <View
                                        key={`${lineIndex}:quote:${quoteContent.slice(0, 12)}`}
                                        style={[
                                            styles.blockquote,
                                            {
                                                borderLeftColor: colors.border,
                                                backgroundColor: colors.surface,
                                            },
                                        ]}
                                    >
                                        <Text selectable style={[styles.text, { color: colors.text }]}>
                                            {renderInlineTokens(tokenizeInline(quoteContent))}
                                        </Text>
                                    </View>
                                );
                            }

                            const effectiveLine = lineShape.content || "";
                            const headingMatch = lineShape.type === "text" ? effectiveLine.match(/^(#{1,6})\s+(.+)$/) : null;
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
                                        {renderInlineTokens(tokenizeInline(headingMatch[2]))}
                                    </Text>
                                );
                            }

                            const lineMedia = resolveMedia(effectiveLine, adminBaseUrl);
                            if (lineMedia?.kind === "image") {
                                return <ImagePreview key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} alt={lineMedia.label} candidates={lineMedia.candidates} />;
                            }
                            if (lineMedia?.kind === "video" || lineMedia?.kind === "audio") {
                                return <MediaPlayer key={`${lineIndex}:${lineMedia.href}`} src={lineMedia.href} type={lineMedia.kind} title={lineMedia.label} candidates={lineMedia.candidates} />;
                            }
                            return (
                                <View
                                    key={`${lineIndex}:${line.slice(0, 12)}`}
                                    style={lineShape.type === "list" ? styles.listRow : undefined}
                                >
                                    {lineShape.type === "list" ? (
                                        <Text style={[styles.listPrefix, { color: colors.textMuted }]}>{lineShape.prefix}</Text>
                                    ) : null}
                                    <Text
                                        selectable
                                        style={[
                                            styles.text,
                                            { color: colors.text },
                                            lineShape.type === "list" ? styles.listText : null,
                                        ]}
                                    >
                                        {renderInlineTokens(tokenizeInline(effectiveLine))}
                                    </Text>
                                </View>
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
    strong: {
        fontWeight: "800",
    },
    em: {
        fontStyle: "italic",
    },
    strike: {
        textDecorationLine: "line-through",
    },
    inlineCode: {
        borderWidth: StyleSheet.hairlineWidth,
        borderRadius: 5,
        fontFamily: "monospace",
        fontSize: 13,
        lineHeight: 20,
        paddingHorizontal: 3,
    },
    link: {
        textDecorationLine: "underline",
    },
    listRow: {
        width: "100%",
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
    },
    listPrefix: {
        width: 20,
        fontSize: 14,
        lineHeight: 21,
        textAlign: "right",
    },
    listText: {
        flex: 1,
    },
    blockquote: {
        borderLeftWidth: 3,
        borderRadius: 8,
        paddingHorizontal: spacing.sm,
        paddingVertical: 6,
    },
    hr: {
        height: StyleSheet.hairlineWidth,
        marginVertical: spacing.xs,
        width: "100%",
        opacity: 0.75,
    },
    table: {
        width: "100%",
        borderWidth: StyleSheet.hairlineWidth,
        borderRadius: 10,
        overflow: "hidden",
    },
    tableRow: {
        flexDirection: "row",
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    tableHeaderRow: {
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    tableHeaderCell: {
        flex: 1,
        fontSize: 12,
        fontWeight: "800",
        lineHeight: 18,
        paddingHorizontal: spacing.sm,
        paddingVertical: 7,
    },
    tableCell: {
        flex: 1,
        fontSize: 12,
        lineHeight: 18,
        paddingHorizontal: spacing.sm,
        paddingVertical: 7,
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
