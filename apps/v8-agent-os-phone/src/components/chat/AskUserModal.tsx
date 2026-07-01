import { memo, useEffect, useMemo, useState } from "react";
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Button } from "@/src/components/ui/button";
import { Textarea } from "@/src/components/ui/textarea";
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

function mediaKey(item: AskUserMedia, index: number) {
    return asText(item.id) || asText(item.artifactId) || asText(item.url) || asText(item.previewUrl) || `media-${index}`;
}

function normalizeQuestions(question: string, request?: AskUserRequest | null): AskUserQuestion[] {
    const source = Array.isArray(request?.questions) ? request?.questions : [];
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
        ...(Array.isArray(request?.media) ? request?.media : []),
        ...(Array.isArray(request?.artifacts) ? request?.artifacts : []),
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
    const [answer, setAnswer] = useState("");
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [selectedOptions, setSelectedOptions] = useState<Record<string, string[]>>({});
    const [selectedMedia, setSelectedMedia] = useState<string[]>([]);

    useEffect(() => {
        if (visible) {
            setAnswer("");
            setExpanded({});
            setSelectedOptions({});
            setSelectedMedia([]);
        }
    }, [visible, toolCallId]);

    if (!visible) return null;

    const toggleOption = (questionItem: AskUserQuestion, option: AskUserOption, index: number) => {
        const qid = asText(questionItem.id) || "answer";
        const key = optionKey(option, index);
        const multi = Boolean(questionItem.multiSelect || questionItem.multiple);
        setSelectedOptions((current) => {
            const values = current[qid] || [];
            const nextValues = values.includes(key)
                ? values.filter((item) => item !== key)
                : multi
                    ? [...values, key]
                    : [key];
            return { ...current, [qid]: nextValues };
        });
    };

    const toggleMedia = (item: AskUserMedia, index: number) => {
        const key = mediaKey(item, index);
        setSelectedMedia((current) => {
            if (current.includes(key)) return current.filter((value) => value !== key);
            return mediaSelectionMode === "multiple" ? [...current, key] : [key];
        });
    };

    const buildAnswer = () => {
        const lines: string[] = [];
        for (const item of questions) {
            const qid = asText(item.id) || "answer";
            const chosen = (selectedOptions[qid] || [])
                .map((key) => {
                    const option = (item.options || []).find((candidate, index) => optionKey(candidate, index) === key);
                    return asText(option?.title) || asText(option?.label) || asText(option?.value) || key;
                })
                .filter(Boolean);
            if (chosen.length) {
                lines.push(`${asText(item.title) || asText(item.question) || qid}: ${chosen.join("、")}`);
            }
        }
        if (selectedMedia.length) lines.push(`选择的参考产物: ${selectedMedia.join("、")}`);
        if (answer.trim()) lines.push(answer.trim());
        return lines.join("\n").trim();
    };

    const hasOptions = questions.some((item) => (item.options || []).length > 0);
    const canSubmit = Boolean(buildAnswer());

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
            <View style={[styles.overlay, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.58)" : "rgba(15,23,42,0.36)" }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onCancel} />
                <View style={[styles.card, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={[styles.headerIcon, { backgroundColor: "rgba(124,58,237,0.12)" }]}>
                            <MaterialCommunityIcons name="message-processing-outline" size={18} color={colors.primary} />
                        </View>
                        <View style={styles.headerText}>
                            <Text numberOfLines={1} style={[styles.title, { color: colors.text }]}>
                                {asText(request?.question) || question || t("src.components.chat.askusermodal.one_quick_answer_before_we_continue")}
                            </Text>
                            {asText(request?.details) ? (
                                <Text numberOfLines={1} style={[styles.subtitle, { color: colors.textMuted }]}>
                                    {asText(request?.details)}
                                </Text>
                            ) : null}
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
                                    const kind = mediaKind(item);
                                    const url = mediaUrl(item);
                                    const title = asText(item.title) || asText(item.name) || key;
                                    return (
                                        <Pressable
                                            key={key}
                                            style={[
                                                styles.mediaThumb,
                                                { borderColor: selected ? colors.primary : colors.border, backgroundColor: colors.surface },
                                            ]}
                                            onPress={() => toggleMedia(item, index)}
                                        >
                                            {kind === "image" && url ? (
                                                <Image source={{ uri: url }} style={styles.mediaImage} resizeMode="cover" />
                                            ) : (
                                                <View style={styles.mediaFallback}>
                                                    <MaterialCommunityIcons
                                                        name={kind === "video" ? "movie-open-outline" : "music-note-outline"}
                                                        size={24}
                                                        color={colors.textMuted}
                                                    />
                                                </View>
                                            )}
                                            <View style={styles.mediaLabel}>
                                                <Text numberOfLines={1} style={styles.mediaLabelText}>{title}</Text>
                                            </View>
                                            {selected ? (
                                                <View style={[styles.checkBadge, { backgroundColor: colors.primary }]}>
                                                    <MaterialCommunityIcons name="check" size={12} color="#FFFFFF" />
                                                </View>
                                            ) : null}
                                        </Pressable>
                                    );
                                })}
                            </ScrollView>
                        ) : null}

                        {questions.map((item, qIndex) => {
                            const qid = asText(item.id) || `q${qIndex + 1}`;
                            const title = asText(item.title) || asText(item.question) || `${qIndex + 1}`;
                            const detail = asText(item.detail) || asText(item.description);
                            const options = item.options || [];
                            const isExpanded = Boolean(expanded[qid]);
                            return (
                                <View key={qid} style={[styles.questionBlock, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                                    <Pressable
                                        style={styles.questionHeader}
                                        onPress={() => setExpanded((current) => ({ ...current, [qid]: !current[qid] }))}
                                    >
                                        <Text numberOfLines={2} style={[styles.questionTitle, { color: colors.text }]}>{title}</Text>
                                        {detail ? (
                                            <MaterialCommunityIcons
                                                name={isExpanded ? "chevron-up" : "chevron-down"}
                                                size={20}
                                                color={colors.textMuted}
                                            />
                                        ) : null}
                                    </Pressable>
                                    {detail && isExpanded ? (
                                        <Text style={[styles.questionDetail, { color: colors.textMuted }]}>{detail}</Text>
                                    ) : null}
                                    {options.length ? (
                                        <View style={styles.optionsGrid}>
                                            {options.map((option, index) => {
                                                const key = optionKey(option, index);
                                                const selected = (selectedOptions[qid] || []).includes(key);
                                                const label = asText(option.title) || asText(option.label) || asText(option.value) || key;
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
                                                        onPress={() => toggleOption(item, option, index)}
                                                    >
                                                        <Text style={[styles.optionText, { color: selected ? colors.primary : colors.text }]}>{label}</Text>
                                                        {selected ? <MaterialCommunityIcons name="check" size={16} color={colors.primary} /> : null}
                                                    </Pressable>
                                                );
                                            })}
                                        </View>
                                    ) : null}
                                </View>
                            );
                        })}

                        <Textarea
                            value={answer}
                            onChangeText={setAnswer}
                            placeholder={hasOptions ? t("src.components.chat.askusermodal.answer_briefly_or_provide_the_missing_information_needed_to_continue") : t("src.components.chat.askusermodal.answer_briefly_or_provide_the_missing_information_needed_to_continue")}
                            style={styles.textarea}
                            editable={!busy}
                        />
                    </ScrollView>

                    <View style={[styles.footer, { borderTopColor: colors.border }]}>
                        <Button variant="ghost" onPress={onCancel} disabled={busy}>
                            {t("src.components.chat.askusermodal.dismiss")}
                        </Button>
                        <Button onPress={() => void onSubmit(toolCallId, buildAnswer(), true)} disabled={busy || !canSubmit}>
                            {busy ? t("src.components.chat.askusermodal.sending") : t("src.components.chat.askusermodal.send_and_continue")}
                        </Button>
                    </View>
                </View>
            </View>
        </Modal>
    );
});

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        paddingHorizontal: 14,
        paddingVertical: 22,
    },
    card: {
        width: "100%",
        maxWidth: 560,
        maxHeight: "88%",
        borderRadius: 26,
        borderWidth: 1,
        overflow: "hidden",
        shadowColor: "#0F172A",
        shadowOpacity: 0.18,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: 14 },
        elevation: 20,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerIcon: {
        width: 36,
        height: 36,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
    },
    headerText: {
        flex: 1,
        minWidth: 0,
    },
    title: {
        fontSize: 16,
        fontWeight: "800",
        lineHeight: 22,
    },
    subtitle: {
        marginTop: 2,
        fontSize: 11,
        lineHeight: 16,
    },
    scroll: {
        maxHeight: 560,
    },
    content: {
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
        gap: spacing.sm,
    },
    mediaStrip: {
        gap: spacing.sm,
        paddingBottom: 2,
    },
    mediaThumb: {
        width: 118,
        height: 88,
        borderWidth: 1,
        borderRadius: 18,
        overflow: "hidden",
    },
    mediaImage: {
        width: "100%",
        height: "100%",
    },
    mediaFallback: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    mediaLabel: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        paddingHorizontal: 8,
        paddingVertical: 5,
        backgroundColor: "rgba(15,23,42,0.68)",
    },
    mediaLabelText: {
        color: "#FFFFFF",
        fontSize: 10,
        fontWeight: "700",
    },
    checkBadge: {
        position: "absolute",
        top: 7,
        right: 7,
        width: 20,
        height: 20,
        borderRadius: 10,
        alignItems: "center",
        justifyContent: "center",
    },
    questionBlock: {
        borderRadius: radii.lg,
        borderWidth: StyleSheet.hairlineWidth,
        paddingHorizontal: 12,
        paddingVertical: 10,
        gap: 8,
    },
    questionHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    questionTitle: {
        flex: 1,
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 20,
    },
    questionDetail: {
        fontSize: 12,
        lineHeight: 18,
    },
    optionsGrid: {
        gap: 8,
    },
    option: {
        minHeight: 42,
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 12,
        paddingVertical: 9,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    optionText: {
        flex: 1,
        fontSize: 13,
        fontWeight: "700",
        lineHeight: 18,
    },
    textarea: {
        minHeight: 92,
        textAlignVertical: "top",
    },
    footer: {
        flexDirection: "row",
        justifyContent: "flex-end",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        borderTopWidth: StyleSheet.hairlineWidth,
    },
});
