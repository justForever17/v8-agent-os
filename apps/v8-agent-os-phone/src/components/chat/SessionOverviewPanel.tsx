import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
    ActivityIndicator,
    Image,
    Modal,
    PanResponder,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import {
    buildSessionOutputProjection,
    buildSubagentReturnProjection,
    type SubagentReturnProjection,
} from "@v8/session-realtime";
import Animated, { SlideInRight } from "react-native-reanimated";

import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { listSessionArtifacts, readSessionWorkbenchFile } from "@/src/lib/phone-api";
import type { PhoneRuntimeStageActivity } from "@/src/lib/runtime-stage";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ArtifactDetail, ChatMessage, WorkbenchFilePage } from "@/src/types/admin";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

type OverviewFile = {
    id: string;
    path: string;
    name: string;
    source: "spec" | "artifact" | "write";
};

const PAGE_LINES = 120;

function text(value: unknown) {
    return String(value || "").trim();
}

function normalizedPath(value: unknown) {
    return text(value).replace(/\\/g, "/");
}

function fileNameOf(value: string) {
    return normalizedPath(value).split("/").filter(Boolean).at(-1) || value;
}

function colorForSubagent(value: string) {
    let hash = 0;
    for (const char of value || "subagent") hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return `hsl(${Math.abs(hash) % 360}, 62%, 48%)`;
}

function SubagentReturnItem({ item, nested = false }: { item: SubagentReturnProjection; nested?: boolean }) {
    const { colors, t } = useUiPrefs();
    const [expanded, setExpanded] = useState(false);
    const statusLabel = ["ok", "completed", "success", "terminated"].includes(item.status)
        ? t("src.components.chat.sessionoverviewpanel.subagent_returned")
        : ["queued", "running", "starting", "streaming", "updated"].includes(item.status)
            ? t("src.components.chat.sessionoverviewpanel.subagent_running")
            : t("src.components.chat.sessionoverviewpanel.subagent_needs_attention");
    return (
        <View style={[styles.subagentItem, nested ? { marginLeft: spacing.lg, borderLeftColor: colors.border, borderLeftWidth: StyleSheet.hairlineWidth } : null]}>
            <Pressable onPress={() => setExpanded((value) => !value)} style={styles.subagentHeader} accessibilityRole="button">
                {item.avatar ? (
                    <Image source={{ uri: item.avatar }} style={styles.subagentAvatarClip} />
                ) : (
                    <View style={[styles.subagentAvatar, { backgroundColor: colorForSubagent(item.family || item.name) }]}>
                        <Text style={styles.subagentAvatarText}>{Array.from(item.name.trim())[0]?.toUpperCase() || "A"}</Text>
                    </View>
                )}
                <View style={styles.subagentBody}>
                    <Text numberOfLines={1} style={[styles.subagentName, { color: colors.text }]}>{item.name}</Text>
                    <Text numberOfLines={1} style={[styles.subagentSummary, { color: colors.textMuted }]}>{item.taskGoal || item.summary || statusLabel}</Text>
                </View>
                <Text style={[styles.subagentStatus, { color: colors.textMuted }]}>{statusLabel}</Text>
                <MaterialCommunityIcons name={expanded ? "chevron-up" : "chevron-down"} size={19} color={colors.textMuted} />
            </Pressable>
            {expanded ? (
                <View style={[styles.subagentDetail, { borderTopColor: colors.border }]}>
                    {item.summary ? <MarkdownRenderer content={item.summary} /> : null}
                    {item.selfCheck ? <Text style={[styles.subagentDetailMuted, { color: colors.textMuted }]}>{t("src.components.chat.sessionoverviewpanel.subagent_self_check")}: {item.selfCheck}</Text> : null}
                    {item.artifactRefs.length ? <Text style={[styles.subagentDetailMuted, { color: colors.textMuted }]}>{t("src.components.chat.sessionoverviewpanel.subagent_artifacts", { count: item.artifactRefs.length })}</Text> : null}
                    {item.children.map((child) => <SubagentReturnItem key={child.id} item={child} nested />)}
                </View>
            ) : null}
        </View>
    );
}

function SubagentReturnsSection({ items }: { items: SubagentReturnProjection[] }) {
    const { colors, t } = useUiPrefs();
    const [expanded, setExpanded] = useState(false);
    if (!items.length) return null;
    return (
        <View style={[styles.subagentSection, { borderColor: colors.border, backgroundColor: colors.surfaceMuted }]}>
            <Pressable onPress={() => setExpanded((value) => !value)} style={styles.subagentSectionHeader} accessibilityRole="button">
                <MaterialCommunityIcons name="account-group-outline" size={19} color={colors.textMuted} />
                <Text style={[styles.subagentSectionTitle, { color: colors.text }]}>{t("src.components.chat.sessionoverviewpanel.subagents")}</Text>
                <Text style={[styles.subagentSectionCount, { color: colors.textMuted }]}>{items.length}</Text>
                <MaterialCommunityIcons name={expanded ? "chevron-up" : "chevron-down"} size={20} color={colors.textMuted} />
            </Pressable>
            {expanded ? <View style={[styles.subagentList, { borderTopColor: colors.border }]}>{items.map((item) => <SubagentReturnItem key={item.id} item={item} />)}</View> : null}
        </View>
    );
}

function FileSnippetRow({
    file,
    sessionId,
    authorizedFetch,
}: {
    file: OverviewFile;
    sessionId: string;
    authorizedFetch: AuthorizedFetch;
}) {
    const { colors, t } = useUiPrefs();
    const [expanded, setExpanded] = useState(false);
    const [startLine, setStartLine] = useState(1);
    const [payload, setPayload] = useState<WorkbenchFilePage | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const loadPage = useCallback(async (nextStart: number) => {
        setLoading(true);
        setError("");
        try {
            const next = await readSessionWorkbenchFile(authorizedFetch, sessionId, file.path, nextStart, PAGE_LINES);
            setPayload(next);
            setStartLine(Math.max(1, Number(next.startLine || nextStart)));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setLoading(false);
        }
    }, [authorizedFetch, file.path, sessionId]);

    useEffect(() => {
        if (expanded && !payload && !loading) void loadPage(1);
    }, [expanded, loadPage, loading, payload]);

    const previousPage = useCallback(() => {
        if (loading || startLine <= 1) return;
        void loadPage(Math.max(1, startLine - PAGE_LINES));
    }, [loadPage, loading, startLine]);
    const nextPage = useCallback(() => {
        if (loading || !payload?.hasMore) return;
        void loadPage(Math.max(1, Number(payload.endLine || startLine) + 1));
    }, [loadPage, loading, payload?.endLine, payload?.hasMore, startLine]);
    const pagePanResponder = useMemo(() => PanResponder.create({
        onMoveShouldSetPanResponder: (_, gesture) => Math.abs(gesture.dx) > 18 && Math.abs(gesture.dx) > Math.abs(gesture.dy),
        onPanResponderRelease: (_, gesture) => {
            if (gesture.dx < -40) nextPage();
            else if (gesture.dx > 40) previousPage();
        },
    }), [nextPage, previousPage]);

    const sourceLabel = file.source === "spec"
        ? t("src.components.chat.sessionoverviewpanel.spec_document")
        : file.source === "artifact"
            ? t("src.components.chat.sessionoverviewpanel.artifact")
            : t("src.components.chat.sessionoverviewpanel.changed_file");

    const sourceLineTone = (line: string) => {
        if (/^\+\+\+|^---/.test(line)) return { backgroundColor: "#17212B", color: "#C9D1D9" };
        if (/^\+/.test(line)) return { backgroundColor: "#0F2A1B", color: "#7EE787" };
        if (/^-/.test(line)) return { backgroundColor: "#3A1518", color: "#FF7B72" };
        if (/^@@/.test(line)) return { backgroundColor: "#13233A", color: "#79C0FF" };
        return { backgroundColor: "transparent", color: "#E6EDF3" };
    };

    return (
        <View style={[styles.fileCard, { borderColor: colors.border, backgroundColor: colors.surfaceMuted }]}>
            <Pressable
                accessibilityRole="button"
                onPress={() => setExpanded((value) => !value)}
                style={styles.fileHeader}
            >
                <MaterialCommunityIcons name="file-document-outline" size={18} color={colors.textMuted} />
                <View style={styles.fileTitleWrap}>
                    <Text numberOfLines={1} style={[styles.fileName, { color: colors.text }]}>{file.name}</Text>
                    <Text numberOfLines={1} style={[styles.filePath, { color: colors.textMuted }]}>{sourceLabel} · {file.path}</Text>
                </View>
                <MaterialCommunityIcons name={expanded ? "chevron-up" : "chevron-down"} size={20} color={colors.textMuted} />
            </Pressable>
            {expanded ? (
                <View style={[styles.snippetWrap, { borderTopColor: colors.border }]} {...pagePanResponder.panHandlers}>
                    {loading && !payload ? <ActivityIndicator color={colors.primary} style={styles.loading} /> : null}
                    {error ? <Text style={[styles.errorText, { color: colors.danger }]}>{error}</Text> : null}
                    {payload?.binary ? (
                        <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t("src.components.chat.sessionoverviewpanel.binary_unavailable")}</Text>
                    ) : null}
                    {!payload?.binary && payload?.lines?.length ? (
                        <ScrollView style={styles.snippetScroll} nestedScrollEnabled showsVerticalScrollIndicator={false}>
                            {payload.lines.map((line) => {
                                const tone = sourceLineTone(line.text || "");
                                return (
                                    <View key={line.number} style={[styles.sourceLine, { backgroundColor: tone.backgroundColor }]}>
                                        <Text style={styles.lineNumber}>{line.number}</Text>
                                        <Text selectable style={[styles.lineText, { color: tone.color }]}>{line.text || " "}</Text>
                                    </View>
                                );
                            })}
                        </ScrollView>
                    ) : null}
                    {!loading && !error && !payload?.binary && payload && !payload.lines?.length ? (
                        <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t("src.components.chat.sessionoverviewpanel.empty_file")}</Text>
                    ) : null}
                    {payload ? (
                        <View style={[styles.pager, { borderTopColor: colors.border }]}>
                            <Pressable disabled={loading || startLine <= 1} onPress={previousPage} style={styles.pagerButton}>
                                <MaterialCommunityIcons name="chevron-left" size={22} color={startLine <= 1 ? colors.textSoft : colors.text} />
                            </Pressable>
                            <Text style={[styles.pageLabel, { color: colors.textMuted }]}>
                                {payload.startLine || 0}–{payload.endLine || 0} / {payload.totalLines ?? "—"}
                            </Text>
                            <Pressable disabled={loading || !payload.hasMore} onPress={nextPage} style={styles.pagerButton}>
                                <MaterialCommunityIcons name="chevron-right" size={22} color={!payload.hasMore ? colors.textSoft : colors.text} />
                            </Pressable>
                        </View>
                    ) : null}
                </View>
            ) : null}
        </View>
    );
}

export const SessionOverviewPanel = memo(function SessionOverviewPanel({
    visible,
    sessionId,
    outputEvidence = [],
    messages,
    runtimeActivities,
    runStatus,
    currentStepTitle,
    authorizedFetch,
    onClose,
}: {
    visible: boolean;
    sessionId: string;
    outputEvidence?: unknown[];
    messages: ChatMessage[];
    runtimeActivities: PhoneRuntimeStageActivity[];
    runStatus?: string;
    currentStepTitle?: string;
    authorizedFetch: AuthorizedFetch;
    onClose: () => void;
}) {
    const { width } = useWindowDimensions();
    const { colors, t } = useUiPrefs();
    const [sessionArtifacts, setSessionArtifacts] = useState<ArtifactDetail[]>([]);
    const [loading, setLoading] = useState(false);
    const files = useMemo<OverviewFile[]>(() => buildSessionOutputProjection(messages, sessionArtifacts, { sessionId, evidence: outputEvidence })
        .filter((output) => Boolean(output.path))
        .map((output) => ({
            id: output.id,
            path: output.path || "",
            name: output.name || fileNameOf(output.path || ""),
            source: output.source,
        })), [messages, outputEvidence, sessionArtifacts, sessionId]);
    const subagentReturns = useMemo(
        () => buildSubagentReturnProjection(messages, runtimeActivities.map((activity) => activity.node)),
        [messages, runtimeActivities],
    );

    useEffect(() => {
        let disposed = false;
        if (!visible || !sessionId.trim()) {
            if (!sessionId.trim()) setSessionArtifacts([]);
            return () => { disposed = true; };
        }
        setLoading(true);
        void listSessionArtifacts(authorizedFetch, sessionId.trim())
            .then((artifacts) => {
                if (!disposed) setSessionArtifacts(artifacts);
            })
            .catch(() => {
                if (!disposed) setSessionArtifacts([]);
            })
            .finally(() => {
                if (!disposed) setLoading(false);
        });
        return () => { disposed = true; };
    }, [authorizedFetch, sessionId, visible]);

    const normalizedStatus = text(runStatus).toLowerCase();
    const statusLabel = ["running", "queued", "pending", "starting", "streaming"].includes(normalizedStatus)
        ? t("src.components.chat.sessionoverviewpanel.running")
        : ["waiting_input", "waiting_approval"].includes(normalizedStatus)
            ? t("src.components.chat.sessionoverviewpanel.waiting")
            : ["failed", "cancelled"].includes(normalizedStatus)
                ? t("src.components.chat.sessionoverviewpanel.needs_attention")
                : t("src.components.chat.sessionoverviewpanel.completed");
    const statusColor = ["failed", "cancelled"].includes(normalizedStatus)
        ? colors.danger
        : ["waiting_input", "waiting_approval"].includes(normalizedStatus)
            ? colors.warning
            : ["running", "queued", "pending", "starting", "streaming"].includes(normalizedStatus)
                ? colors.success
                : colors.textMuted;

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
            <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <Animated.View
                    entering={SlideInRight.duration(220)}
                    style={[styles.panel, { width: Math.min(440, Math.max(300, width * 0.9)), backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                >
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={styles.headerTitleWrap}>
                            <Text style={[styles.title, { color: colors.text }]}>{t("src.components.chat.sessionoverviewpanel.title")}</Text>
                            <Text style={[styles.subtitle, { color: colors.textMuted }]}>{files.length} {t("src.components.chat.sessionoverviewpanel.files")}</Text>
                        </View>
                        <Pressable accessibilityRole="button" accessibilityLabel={t("src.components.chat.sessionoverviewpanel.close")} onPress={onClose} style={styles.closeButton}>
                            <MaterialCommunityIcons name="close" size={21} color={colors.textMuted} />
                        </Pressable>
                    </View>
                    <View style={[styles.statusCard, { borderBottomColor: colors.border }]}>
                        <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
                        <View style={styles.statusTextWrap}>
                            <Text style={[styles.statusLabel, { color: colors.text }]}>{statusLabel}</Text>
                            {currentStepTitle ? <Text numberOfLines={2} style={[styles.statusDetail, { color: colors.textMuted }]}>{currentStepTitle}</Text> : null}
                        </View>
                    </View>
                    <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
                        <SubagentReturnsSection items={subagentReturns} />
                        {loading && files.length === 0 ? <ActivityIndicator color={colors.primary} style={styles.loading} /> : null}
                        {!loading && files.length === 0 ? (
                            <View style={styles.emptyState}>
                                <MaterialCommunityIcons name="file-outline" size={28} color={colors.textSoft} />
                                <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t("src.components.chat.sessionoverviewpanel.no_files")}</Text>
                            </View>
                        ) : null}
                        {files.map((file) => (
                            <FileSnippetRow key={`${file.source}:${file.path}`} file={file} sessionId={sessionId} authorizedFetch={authorizedFetch} />
                        ))}
                    </ScrollView>
                </Animated.View>
            </View>
        </Modal>
    );
});

const styles = StyleSheet.create({
    overlay: { flex: 1, alignItems: "flex-end" },
    panel: { height: "100%", borderLeftWidth: StyleSheet.hairlineWidth },
    header: { minHeight: 70, paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: "row", alignItems: "center" },
    headerTitleWrap: { flex: 1, gap: 3 },
    title: { fontSize: 18, fontWeight: "700" },
    subtitle: { fontSize: 12 },
    closeButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
    statusCard: { minHeight: 62, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: "row", alignItems: "flex-start", gap: spacing.sm },
    statusDot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
    statusTextWrap: { flex: 1, gap: 4 },
    statusLabel: { fontSize: 14, fontWeight: "700" },
    statusDetail: { fontSize: 12, lineHeight: 18 },
    content: { padding: spacing.md, gap: spacing.sm, paddingBottom: 40 },
    fileCard: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.lg, overflow: "hidden" },
    fileHeader: { minHeight: 58, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, flexDirection: "row", alignItems: "center", gap: spacing.sm },
    fileTitleWrap: { flex: 1, gap: 4 },
    fileName: { fontSize: 14, fontWeight: "700" },
    filePath: { fontSize: 11 },
    snippetWrap: { borderTopWidth: StyleSheet.hairlineWidth, backgroundColor: "#0D1117" },
    snippetScroll: { maxHeight: 360, paddingVertical: spacing.sm, backgroundColor: "#0D1117" },
    sourceLine: { flexDirection: "row", alignItems: "flex-start", minHeight: 20 },
    lineNumber: { width: 46, paddingHorizontal: 8, textAlign: "right", fontSize: 11, lineHeight: 18, color: "#7D8590", backgroundColor: "#161B22" },
    lineText: { flex: 1, paddingHorizontal: 8, fontSize: 12, lineHeight: 18 },
    pager: { height: 44, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.sm },
    pagerButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
    pageLabel: { fontSize: 11 },
    loading: { paddingVertical: spacing.xl },
    errorText: { padding: spacing.md, fontSize: 12, lineHeight: 18 },
    emptyState: { minHeight: 180, alignItems: "center", justifyContent: "center", gap: spacing.sm },
    emptyText: { padding: spacing.md, textAlign: "center", fontSize: 12, lineHeight: 18 },
    subagentSection: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.lg, overflow: "hidden" },
    subagentSectionHeader: { minHeight: 52, paddingHorizontal: spacing.md, flexDirection: "row", alignItems: "center", gap: spacing.sm },
    subagentSectionTitle: { flex: 1, fontSize: 14, fontWeight: "700" },
    subagentSectionCount: { fontSize: 11 },
    subagentList: { borderTopWidth: StyleSheet.hairlineWidth },
    subagentItem: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "transparent" },
    subagentHeader: { minHeight: 56, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, flexDirection: "row", alignItems: "center", gap: spacing.sm },
    subagentAvatar: { width: 30, height: 30, borderRadius: 10, alignItems: "center", justifyContent: "center" },
    subagentAvatarClip: { width: 30, height: 30, borderRadius: 10, overflow: "hidden" },
    subagentAvatarText: { color: "white", fontSize: 12, fontWeight: "700" },
    subagentBody: { flex: 1, minWidth: 0, gap: 3 },
    subagentName: { fontSize: 13, fontWeight: "700" },
    subagentSummary: { fontSize: 11 },
    subagentStatus: { fontSize: 10 },
    subagentDetail: { borderTopWidth: StyleSheet.hairlineWidth, padding: spacing.md, gap: spacing.sm },
    subagentDetailMuted: { fontSize: 11, lineHeight: 17 },
});
