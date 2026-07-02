import { memo, useMemo, useState } from "react";
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { usePreparedPhoneMediaSource } from "@/src/lib/phone-media-source";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type AskUserOption = {
    id?: string;
    value?: string;
    title?: string;
    label?: string;
    detail?: string;
    description?: string;
};

type AskUserQuestion = {
    id?: string;
    title?: string;
    label?: string;
    question?: string;
    detail?: string;
    description?: string;
    multiSelect?: boolean;
    multiple?: boolean;
    options?: AskUserOption[];
};

type AskUserMedia = {
    id?: string;
    artifactId?: string;
    title?: string;
    name?: string;
    type?: string;
    kind?: string;
    mimeType?: string;
    url?: string;
    href?: string;
    previewUrl?: string;
    thumbnailUrl?: string;
    contentUrl?: string;
};

type AskUserRequest = Record<string, unknown> & {
    question?: string;
    prompt?: string;
    details?: string;
    questions?: AskUserQuestion[];
    media?: AskUserMedia[];
    artifacts?: AskUserMedia[];
    selectionMode?: string;
};

type AskUserModalProps = {
    visible: boolean;
    question: string;
    request?: AskUserRequest | null;
    toolCallId: string;
    busy?: boolean;
    onSubmit: (toolCallId: string, answer: string, approve: boolean) => void | Promise<void>;
    onCancel?: () => void;
};

function asText(value: unknown) {
    return typeof value === "string" ? value.trim() : "";
}

function optionKey(option: AskUserOption, index: number) {
    return asText(option.id) || asText(option.value) || asText(option.title) || asText(option.label) || `option-${index}`;
}

function optionLabel(option: AskUserOption, index: number) {
    return asText(option.title) || asText(option.label) || asText(option.value) || `option-${index + 1}`;
}

function mediaKey(item: AskUserMedia, index: number) {
    return asText(item.id) || asText(item.artifactId) || asText(item.url) || asText(item.previewUrl) || `media-${index}`;
}

function mediaLabel(item: AskUserMedia, index: number) {
    return asText(item.title) || asText(item.name) || asText(item.artifactId) || `media-${index + 1}`;
}

function questionKey(questionItem: AskUserQuestion, index: number) {
    return asText(questionItem.id) || asText(questionItem.title) || asText(questionItem.question) || `q${index + 1}`;
}

function normalizeQuestions(question: string, request?: AskUserRequest | null): AskUserQuestion[] {
    const source = Array.isArray(request?.questions) ? request.questions : [];
    const normalized = source
        .filter((item) => item && typeof item === "object")
        .map((item, index) => ({
            ...item,
            id: asText(item.id) || `q${index + 1}`,
            title: asText(item.title) || asText(item.label) || asText(item.question) || `${index + 1}`,
            detail: asText(item.detail) || asText(item.description),
            options: Array.isArray(item.options) ? item.options.filter((option) => option && typeof option === "object") : [],
        }));
    if (normalized.length) return normalized;
    return [{
        id: "answer",
        title: asText(request?.question) || asText(request?.prompt) || question,
        detail: asText(request?.details),
        options: [],
    }];
}

function normalizeMedia(request?: AskUserRequest | null) {
    const merged = [
        ...(Array.isArray(request?.media) ? request.media : []),
        ...(Array.isArray(request?.artifacts) ? request.artifacts : []),
    ];
    const seen = new Set<string>();
    return merged.filter((item, index) => {
        if (!item || typeof item !== "object") return false;
        const key = mediaKey(item, index);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function mediaKind(item: AskUserMedia) {
    const text = `${asText(item.type)} ${asText(item.kind)} ${asText(item.mimeType)}`.toLowerCase();
    if (text.includes("video")) return "video";
    if (text.includes("audio")) return "audio";
    return "image";
}

function mediaUrl(item: AskUserMedia) {
    return asText(item.previewUrl) || asText(item.thumbnailUrl) || asText(item.url) || asText(item.contentUrl) || asText(item.href);
}

function mediaPlaybackUrl(item: AskUserMedia) {
    const direct = asText(item.contentUrl) || asText(item.url) || asText(item.href) || asText(item.previewUrl);
    if (direct) return direct;
    const artifactId = asText(item.artifactId) || asText(item.id);
    return artifactId ? `/api/client/artifacts/${encodeURIComponent(artifactId)}/content` : "";
}

function AskUserMediaCard({
    item,
    index,
    selected,
    onToggle,
}: {
    item: AskUserMedia;
    index: number;
    selected: boolean;
    onToggle: () => void;
}) {
    const { colors } = useUiPrefs();
    const kind = mediaKind(item);
    const previewUrl = mediaUrl(item);
    const playbackUrl = mediaPlaybackUrl(item);
    const label = mediaLabel(item, index);
    const [playing, setPlaying] = useState(false);
    const { resolvedSrc } = usePreparedPhoneMediaSource({ src: playbackUrl, title: label });
    const canPlay = Boolean(resolvedSrc && (kind === "audio" || kind === "video"));

    return (
        <Pressable
            style={[
                styles.mediaThumb,
                { borderColor: selected ? colors.primary : colors.border, backgroundColor: colors.surface },
            ]}
            onPress={onToggle}
        >
            {playing && canPlay ? (
                <WebView
                    source={{ html: buildAskUserMediaHtml(resolvedSrc, kind) }}
                    style={styles.mediaInlinePlayer}
                    allowsInlineMediaPlayback
                    mediaPlaybackRequiresUserAction={false}
                    allowFileAccess
                    allowFileAccessFromFileURLs
                    allowUniversalAccessFromFileURLs
                    scrollEnabled={false}
                />
            ) : kind === "image" && previewUrl ? (
                <Image source={{ uri: previewUrl }} style={styles.mediaImage} resizeMode="cover" />
            ) : (
                <View style={styles.mediaFallback}>
                    <MaterialCommunityIcons
                        name={kind === "video" ? "movie-open-outline" : "music-note-outline"}
                        size={20}
                        color={colors.textMuted}
                    />
                </View>
            )}
            <View style={styles.mediaLabel}>
                <Text numberOfLines={1} style={styles.mediaLabelText}>{label}</Text>
            </View>
            {canPlay ? (
                <Pressable
                    onPress={(event) => {
                        event.stopPropagation();
                        setPlaying((current) => !current);
                    }}
                    style={styles.mediaPlayButton}
                    hitSlop={8}
                >
                    <MaterialCommunityIcons name={playing ? "pause" : "play"} size={13} color="#FFFFFF" />
                </Pressable>
            ) : null}
            {selected ? (
                <View style={[styles.checkBadge, { backgroundColor: colors.primary }]}>
                    <MaterialCommunityIcons name="check" size={11} color="#FFFFFF" />
                </View>
            ) : null}
        </Pressable>
    );
}

function buildAskUserMediaHtml(src: string, kind: string) {
    const tag = kind === "audio" ? "audio" : "video";
    return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <style>
      html, body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:#0f172a; }
      ${tag} { width:100%; height:100%; object-fit:cover; }
      audio { padding: 8px; box-sizing: border-box; }
    </style>
  </head>
  <body>
    <${tag} id="player" controls autoplay playsinline preload="metadata"></${tag}>
    <script>
      document.getElementById("player").src = ${JSON.stringify(src)};
    </script>
  </body>
</html>`;
}

export const AskUserModal = memo(function AskUserModal({
    visible,
    question,
    request,
    toolCallId,
    busy = false,
    onSubmit,
    onCancel,
}: AskUserModalProps) {
    const { colors, t, themeMode } = useUiPrefs();
    const questions = useMemo(() => normalizeQuestions(question, request), [question, request]);
    const mediaItems = useMemo(() => normalizeMedia(request), [request]);
    const mediaSelectionMode = asText(request?.selectionMode).toLowerCase() === "multiple" ? "multiple" : "single";
    const [pageIndex, setPageIndex] = useState(0);
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [selectedOptions, setSelectedOptions] = useState<Record<string, string[]>>({});
    const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
    const [selectedMedia, setSelectedMedia] = useState<string[]>([]);

    if (!visible) return null;

    const resetForm = () => {
        setPageIndex(0);
        setExpanded({});
        setSelectedOptions({});
        setCustomAnswers({});
        setSelectedMedia([]);
    };

    const closePanel = () => {
        resetForm();
        onCancel?.();
    };

    const goToPage = (nextIndex: number) => {
        setPageIndex(Math.min(Math.max(nextIndex, 0), Math.max(questions.length - 1, 0)));
    };

    const toggleOption = (questionItem: AskUserQuestion, qIndex: number, option: AskUserOption, index: number) => {
        const qid = questionKey(questionItem, qIndex);
        const key = optionKey(option, index);
        const multi = Boolean(questionItem.multiSelect || questionItem.multiple);
        const alreadySelected = (selectedOptions[qid] || []).includes(key);
        const selectedAfterClick = !alreadySelected;
        setSelectedOptions((current) => {
            const values = current[qid] || [];
            const nextValues = alreadySelected
                ? values.filter((item) => item !== key)
                : multi
                    ? [...values, key]
                    : [key];
            return { ...current, [qid]: nextValues };
        });
        if (!multi && selectedAfterClick && qIndex < questions.length - 1) {
            setTimeout(() => goToPage(qIndex + 1), 180);
        }
    };

    const toggleMedia = (item: AskUserMedia, index: number) => {
        const key = mediaKey(item, index);
        setSelectedMedia((current) => {
            if (current.includes(key)) return current.filter((value) => value !== key);
            return mediaSelectionMode === "multiple" ? [...current, key] : [key];
        });
    };

    const isQuestionAnswered = (item: AskUserQuestion, index: number) => {
        const qid = questionKey(item, index);
        return Boolean((selectedOptions[qid] || []).length || asText(customAnswers[qid]));
    };

    const buildAnswer = () => {
        const lines: string[] = [];
        for (const [index, item] of questions.entries()) {
            const qid = questionKey(item, index);
            const chosen = (selectedOptions[qid] || [])
                .map((key) => {
                    const optionIndex = (item.options || []).findIndex((candidate, candidateIndex) => optionKey(candidate, candidateIndex) === key);
                    const option = optionIndex >= 0 ? (item.options || [])[optionIndex] : null;
                    return option ? optionLabel(option, optionIndex) : key;
                })
                .filter(Boolean);
            const custom = asText(customAnswers[qid]);
            const parts = [...chosen];
            if (custom) parts.push(custom);
            if (parts.length) {
                lines.push(`${index + 1}. ${asText(item.title) || asText(item.question) || qid}: ${parts.join("；")}`);
            }
        }
        if (selectedMedia.length) {
            const labels = selectedMedia.map((key) => {
                const foundIndex = mediaItems.findIndex((item, index) => mediaKey(item, index) === key);
                return foundIndex >= 0 ? mediaLabel(mediaItems[foundIndex], foundIndex) : key;
            });
            lines.unshift(`${t("src.components.chat.askusermodal.selected_media")}: ${labels.join("、")}`);
        }
        return lines.join("\n").trim();
    };

    const currentQuestion = questions[Math.min(pageIndex, Math.max(questions.length - 1, 0))];
    const currentQuestionKey = currentQuestion ? questionKey(currentQuestion, pageIndex) : "answer";
    const currentOptions = currentQuestion?.options || [];
    const currentDetail = asText(currentQuestion?.detail) || asText(currentQuestion?.description);
    const currentMulti = Boolean(currentQuestion?.multiSelect || currentQuestion?.multiple);
    const currentAnswered = currentQuestion ? isQuestionAnswered(currentQuestion, pageIndex) : true;
    const isLastPage = pageIndex >= questions.length - 1;
    const canSubmit = Boolean(buildAnswer()) && questions.every(isQuestionAnswered);
    const title = asText(request?.question) || question || t("src.components.chat.askusermodal.one_quick_answer_before_we_continue");
    const details = asText(request?.details);

    return (
        <Modal visible={visible} transparent animationType="slide" onRequestClose={closePanel}>
            <View style={[styles.overlay, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.32)" : "rgba(15,23,42,0.16)" }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={closePanel} />
                <View style={[styles.sheet, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={styles.headerText}>
                            <Text numberOfLines={1} style={[styles.title, { color: colors.text }]}>{title}</Text>
                            {details ? <Text numberOfLines={1} style={[styles.subtitle, { color: colors.textMuted }]}>{details}</Text> : null}
                        </View>
                        <View style={styles.headerActions}>
                            <Pressable onPress={() => goToPage(pageIndex - 1)} disabled={pageIndex <= 0} style={styles.iconButton}>
                                <MaterialCommunityIcons name="chevron-left" size={20} color={pageIndex <= 0 ? colors.textMuted : colors.text} />
                            </Pressable>
                            <Text style={[styles.counter, { color: colors.textMuted }]}>{pageIndex + 1}/{Math.max(questions.length, 1)}</Text>
                            <Pressable onPress={() => goToPage(pageIndex + 1)} disabled={pageIndex >= questions.length - 1} style={styles.iconButton}>
                                <MaterialCommunityIcons name="chevron-right" size={20} color={pageIndex >= questions.length - 1 ? colors.textMuted : colors.text} />
                            </Pressable>
                            <Pressable onPress={closePanel} style={styles.iconButton}>
                                <MaterialCommunityIcons name="close" size={18} color={colors.textMuted} />
                            </Pressable>
                        </View>
                    </View>

                    <ScrollView
                        style={styles.scroll}
                        contentContainerStyle={styles.content}
                        nestedScrollEnabled
                        keyboardShouldPersistTaps="handled"
                        showsVerticalScrollIndicator
                    >
                        {mediaItems.length ? (
                            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.mediaStrip}>
                                {mediaItems.map((item, index) => {
                                    const key = mediaKey(item, index);
                                    const selected = selectedMedia.includes(key);
                                    return (
                                        <AskUserMediaCard
                                            key={key}
                                            item={item}
                                            index={index}
                                            selected={selected}
                                            onToggle={() => toggleMedia(item, index)}
                                        />
                                    );
                                })}
                            </ScrollView>
                        ) : null}

                        {currentQuestion ? (
                            <View style={[styles.questionBlock, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                                <Pressable
                                    style={styles.questionHeader}
                                    onPress={() => setExpanded((current) => ({ ...current, [currentQuestionKey]: !current[currentQuestionKey] }))}
                                >
                                    <Text numberOfLines={2} style={[styles.questionTitle, { color: colors.text }]}>
                                        {asText(currentQuestion.title) || asText(currentQuestion.question) || `${pageIndex + 1}`}
                                    </Text>
                                    {currentMulti ? (
                                        <Text style={[styles.multiBadge, { color: colors.textMuted, backgroundColor: colors.surfaceStrong }]}>
                                            {t("src.components.chat.askusermodal.multi")}
                                        </Text>
                                    ) : null}
                                    {currentDetail ? (
                                        <MaterialCommunityIcons
                                            name={expanded[currentQuestionKey] ? "chevron-up" : "chevron-down"}
                                            size={18}
                                            color={colors.textMuted}
                                        />
                                    ) : null}
                                </Pressable>
                                {currentDetail && expanded[currentQuestionKey] ? (
                                    <Text style={[styles.questionDetail, { color: colors.textMuted }]}>{currentDetail}</Text>
                                ) : null}
                                {currentOptions.length ? (
                                    <View style={styles.optionsGrid}>
                                        {currentOptions.map((option, index) => {
                                            const key = optionKey(option, index);
                                            const selected = (selectedOptions[currentQuestionKey] || []).includes(key);
                                            return (
                                                <Pressable
                                                    key={key}
                                                    style={[
                                                        styles.option,
                                                        {
                                                            borderColor: selected ? colors.primary : colors.border,
                                                            backgroundColor: selected ? "rgba(124,58,237,0.12)" : colors.surfaceStrong,
                                                        },
                                                    ]}
                                                    onPress={() => toggleOption(currentQuestion, pageIndex, option, index)}
                                                >
                                                    <Text numberOfLines={1} style={[styles.optionText, { color: selected ? colors.primary : colors.text }]}>
                                                        {optionLabel(option, index)}
                                                    </Text>
                                                    {selected ? <MaterialCommunityIcons name="check" size={14} color={colors.primary} /> : null}
                                                </Pressable>
                                            );
                                        })}
                                    </View>
                                ) : null}
                                <TextInput
                                    value={customAnswers[currentQuestionKey] || ""}
                                    onChangeText={(value) => setCustomAnswers((current) => ({ ...current, [currentQuestionKey]: value }))}
                                    placeholder={currentOptions.length ? t("src.components.chat.askusermodal.other_or_note") : t("src.components.chat.askusermodal.type_your_answer")}
                                    placeholderTextColor={colors.textMuted}
                                    style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surfaceStrong }]}
                                    editable={!busy}
                                />
                            </View>
                        ) : null}
                    </ScrollView>

                    <View style={[styles.footer, { borderTopColor: colors.border }]}>
                        <Pressable onPress={closePanel} disabled={busy} style={styles.secondaryButton}>
                            <Text style={[styles.secondaryButtonText, { color: colors.textMuted }]}>
                                {t("src.components.chat.askusermodal.dismiss")}
                            </Text>
                        </Pressable>
                        <Pressable
                            onPress={() => {
                                if (!isLastPage) {
                                    goToPage(pageIndex + 1);
                                    return;
                                }
                                const answer = buildAnswer();
                                resetForm();
                                void onSubmit(toolCallId, answer, true);
                            }}
                            disabled={busy || (!isLastPage && !currentAnswered) || (isLastPage && !canSubmit)}
                            style={[styles.primaryButton, { backgroundColor: colors.primary, opacity: busy || (!isLastPage && !currentAnswered) || (isLastPage && !canSubmit) ? 0.45 : 1 }]}
                        >
                            <Text style={styles.primaryButtonText}>
                                {isLastPage ? (busy ? t("src.components.chat.askusermodal.sending") : t("src.components.chat.askusermodal.send_and_continue")) : t("src.components.chat.askusermodal.next")}
                            </Text>
                        </Pressable>
                    </View>
                </View>
            </View>
        </Modal>
    );
});

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        justifyContent: "flex-end",
    },
    sheet: {
        maxHeight: "72%",
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        borderWidth: 1,
        overflow: "hidden",
        shadowColor: "#0F172A",
        shadowOpacity: 0.16,
        shadowRadius: 18,
        shadowOffset: { width: 0, height: -8 },
        elevation: 18,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: 10,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerText: {
        flex: 1,
        minWidth: 0,
    },
    title: {
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 20,
    },
    subtitle: {
        marginTop: 1,
        fontSize: 11,
        lineHeight: 15,
    },
    headerActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 2,
    },
    iconButton: {
        padding: 4,
    },
    counter: {
        minWidth: 28,
        textAlign: "center",
        fontSize: 11,
        fontWeight: "700",
    },
    scroll: {
        maxHeight: 420,
    },
    content: {
        paddingHorizontal: spacing.md,
        paddingVertical: 10,
        gap: spacing.sm,
    },
    mediaStrip: {
        gap: 8,
        paddingBottom: 2,
    },
    mediaThumb: {
        width: 98,
        height: 62,
        borderWidth: 1,
        borderRadius: 14,
        overflow: "hidden",
    },
    mediaImage: {
        width: "100%",
        height: "100%",
    },
    mediaInlinePlayer: {
        width: "100%",
        height: "100%",
        backgroundColor: "#0F172A",
    },
    mediaFallback: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    mediaPlayButton: {
        position: "absolute",
        top: 5,
        left: 5,
        width: 24,
        height: 24,
        borderRadius: 12,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(15,23,42,0.68)",
    },
    mediaLabel: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        paddingHorizontal: 6,
        paddingVertical: 4,
        backgroundColor: "rgba(15,23,42,0.68)",
    },
    mediaLabelText: {
        color: "#FFFFFF",
        fontSize: 9,
        fontWeight: "700",
    },
    checkBadge: {
        position: "absolute",
        top: 5,
        right: 5,
        width: 18,
        height: 18,
        borderRadius: 9,
        alignItems: "center",
        justifyContent: "center",
    },
    questionBlock: {
        borderRadius: radii.lg,
        borderWidth: StyleSheet.hairlineWidth,
        paddingHorizontal: 10,
        paddingVertical: 9,
        gap: 8,
    },
    questionHeader: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    questionTitle: {
        flex: 1,
        fontSize: 13,
        fontWeight: "800",
        lineHeight: 18,
    },
    multiBadge: {
        borderRadius: 999,
        overflow: "hidden",
        paddingHorizontal: 7,
        paddingVertical: 2,
        fontSize: 10,
        fontWeight: "700",
    },
    questionDetail: {
        fontSize: 11,
        lineHeight: 17,
    },
    optionsGrid: {
        gap: 6,
    },
    option: {
        height: 36,
        borderWidth: 1,
        borderRadius: 12,
        paddingHorizontal: 10,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    optionText: {
        flex: 1,
        fontSize: 12,
        fontWeight: "700",
    },
    input: {
        height: 34,
        borderWidth: 1,
        borderRadius: 12,
        paddingHorizontal: 10,
        fontSize: 12,
    },
    footer: {
        flexDirection: "row",
        justifyContent: "flex-end",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: 9,
        borderTopWidth: StyleSheet.hairlineWidth,
    },
    secondaryButton: {
        paddingHorizontal: 12,
        paddingVertical: 8,
    },
    secondaryButtonText: {
        fontSize: 12,
        fontWeight: "700",
    },
    primaryButton: {
        minWidth: 84,
        alignItems: "center",
        borderRadius: 12,
        paddingHorizontal: 14,
        paddingVertical: 8,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontSize: 12,
        fontWeight: "800",
    },
});
