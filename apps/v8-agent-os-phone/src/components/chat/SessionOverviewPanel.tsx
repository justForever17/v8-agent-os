import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
    ActivityIndicator,
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

import { listSpecs, readSessionWorkbenchFile } from "@/src/lib/phone-api";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage, SpecSummary, WorkbenchFilePage } from "@/src/types/admin";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

type OverviewFile = {
    id: string;
    path: string;
    name: string;
    source: "spec" | "artifact" | "change";
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

function pathFromUnknown(value: unknown, depth = 0): string {
    if (depth > 4 || value == null) return "";
    if (typeof value === "string") {
        const candidate = normalizedPath(value);
        return /(?:^|\/)[^/]+\.[A-Za-z0-9]{1,12}(?:$|[?#])/.test(candidate) ? candidate : "";
    }
    if (Array.isArray(value)) {
        for (const item of value) {
            const found = pathFromUnknown(item, depth + 1);
            if (found) return found;
        }
        return "";
    }
    if (typeof value !== "object") return "";
    const record = value as Record<string, unknown>;
    for (const key of ["workspaceRelativePath", "workspace_relative_path", "sourcePath", "source_path", "targetPath", "target_path", "file", "path"]) {
        const found = pathFromUnknown(record[key], depth + 1);
        if (found) return found;
    }
    return "";
}

function collectSpecFiles(specs: SpecSummary[]) {
    const files: OverviewFile[] = [];
    for (const spec of specs) {
        for (const [stage, document] of Object.entries(spec.documents || {})) {
            const path = normalizedPath(document.relativePath);
            if (!path) continue;
            files.push({
                id: `spec:${spec.specId || spec.specDir || "unknown"}:${stage}:${path}`,
                path,
                name: fileNameOf(path),
                source: "spec",
            });
        }
        for (const rawDirectory of spec.targetOutputDirectories || []) {
            const directory = normalizedPath(rawDirectory).replace(/\/$/, "");
            if (!directory) continue;
            for (const rawFile of spec.explicitDeliverableFiles || []) {
                const name = fileNameOf(rawFile);
                if (!name) continue;
                const path = normalizedPath(`${directory}/${name}`);
                files.push({
                    id: `spec-deliverable:${spec.specId || directory}:${path}`,
                    path,
                    name,
                    source: "artifact",
                });
            }
        }
    }
    return files;
}

function collectMessageFiles(messages: ChatMessage[]) {
    const files: OverviewFile[] = [];
    for (const message of messages) {
        const specBrief = message.metadata?.specBrief && typeof message.metadata.specBrief === "object"
            ? message.metadata.specBrief as SpecSummary
            : message.metadata?.spec_brief && typeof message.metadata.spec_brief === "object"
                ? message.metadata.spec_brief as SpecSummary
                : null;
        if (specBrief) files.push(...collectSpecFiles([specBrief]));
        for (const artifact of message.artifacts || []) {
            const path = normalizedPath(
                artifact.workspaceRelativePath
                || artifact.sourcePath
                || artifact.canonicalPath
                || artifact.workspacePath,
            );
            if (!path) continue;
            files.push({ id: `artifact:${artifact.id || artifact.artifactId || path}`, path, name: fileNameOf(path), source: "artifact" });
        }
        for (const node of message.nodes || []) {
            if (node.kind === "artifact") {
                const artifact = node.artifact;
                const path = normalizedPath(
                    artifact.workspaceRelativePath
                    || artifact.sourcePath
                    || artifact.canonicalPath
                    || artifact.workspacePath,
                );
                if (path) files.push({ id: `node-artifact:${node.id}:${path}`, path, name: fileNameOf(path), source: "artifact" });
                continue;
            }
            if (node.kind !== "execution" || !/(write|edit|patch|file)/i.test(text(node.toolName))) continue;
            const path = pathFromUnknown(node.data) || pathFromUnknown(node.args) || pathFromUnknown(node.result);
            if (path) files.push({ id: `change:${node.id}:${path}`, path, name: fileNameOf(path), source: "change" });
        }
    }
    return files;
}

function mergeFiles(...groups: OverviewFile[][]) {
    const byPath = new Map<string, OverviewFile>();
    for (const file of groups.flat()) {
        const key = normalizedPath(file.path).toLowerCase();
        if (!key) continue;
        const existing = byPath.get(key);
        if (!existing || existing.source === "change") byPath.set(key, file);
    }
    return Array.from(byPath.values()).slice(-40).reverse();
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
                            {payload.lines.map((line) => (
                                <View key={line.number} style={styles.sourceLine}>
                                    <Text style={[styles.lineNumber, { color: colors.textSoft }]}>{line.number}</Text>
                                    <Text selectable style={[styles.lineText, { color: colors.text }]}>{line.text || " "}</Text>
                                </View>
                            ))}
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
    workspacePath,
    messages,
    runStatus,
    currentStepTitle,
    authorizedFetch,
    onClose,
}: {
    visible: boolean;
    sessionId: string;
    workspacePath: string;
    messages: ChatMessage[];
    runStatus?: string;
    currentStepTitle?: string;
    authorizedFetch: AuthorizedFetch;
    onClose: () => void;
}) {
    const { width } = useWindowDimensions();
    const { colors, t } = useUiPrefs();
    const [specFiles, setSpecFiles] = useState<OverviewFile[]>([]);
    const [loading, setLoading] = useState(false);
    const messageFiles = useMemo(() => collectMessageFiles(messages), [messages]);
    const files = useMemo(() => mergeFiles(specFiles, messageFiles), [messageFiles, specFiles]);

    useEffect(() => {
        let disposed = false;
        if (!visible || !workspacePath.trim()) {
            if (!workspacePath.trim()) setSpecFiles([]);
            return () => { disposed = true; };
        }
        setLoading(true);
        void listSpecs(authorizedFetch, workspacePath.trim())
            .then((specs) => {
                if (!disposed) setSpecFiles(collectSpecFiles(specs));
            })
            .catch(() => {
                if (!disposed) setSpecFiles([]);
            })
            .finally(() => {
                if (!disposed) setLoading(false);
            });
        return () => { disposed = true; };
    }, [authorizedFetch, visible, workspacePath]);

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
                <View style={[styles.panel, { width: Math.min(440, Math.max(300, width * 0.9)), backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
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
                </View>
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
    snippetWrap: { borderTopWidth: StyleSheet.hairlineWidth },
    snippetScroll: { maxHeight: 360, paddingHorizontal: spacing.sm, paddingVertical: spacing.sm },
    sourceLine: { flexDirection: "row", alignItems: "flex-start", minHeight: 20 },
    lineNumber: { width: 42, paddingRight: 8, textAlign: "right", fontSize: 11, lineHeight: 18 },
    lineText: { flex: 1, fontSize: 12, lineHeight: 18 },
    pager: { height: 44, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.sm },
    pagerButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
    pageLabel: { fontSize: 11 },
    loading: { paddingVertical: spacing.xl },
    errorText: { padding: spacing.md, fontSize: 12, lineHeight: 18 },
    emptyState: { minHeight: 180, alignItems: "center", justifyContent: "center", gap: spacing.sm },
    emptyText: { padding: spacing.md, textAlign: "center", fontSize: 12, lineHeight: 18 },
});
