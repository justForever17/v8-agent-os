import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Linking,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { Redirect, router, useLocalSearchParams, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { coerceAdminResourceRef, resolveAdminResourceUrl } from "@v8/session-realtime";

import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";
import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { openCachedFile, saveResponseToCache } from "@/src/lib/file-transfer";
import { BoundedResponseTextError, readBoundedResponseText } from "@/src/lib/bounded-response-text";
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import {
    fetchArtifactContentResponse,
    getArtifact,
    getSessionScope,
    listArtifacts,
    readSessionWorkbenchFile,
} from "@/src/lib/phone-api";
import { formatClock, formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ArtifactDetail, PhoneUiArtifactNode } from "@/src/types/admin";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

const MAX_INLINE_TEXT_CHARS = 200_000;
const MAX_INLINE_TEXT_BYTES = MAX_INLINE_TEXT_CHARS * 4;

function normalizeParam(value: string | string[] | undefined) {
    if (Array.isArray(value)) return value[0] || "";
    return value || "";
}

function artifactTextKind(artifact: ArtifactDetail) {
    const probe = [
        artifact.mimeType,
        artifact.title,
        artifact.displayLabel,
        artifact.workspaceRelativePath,
        artifact.workspacePath,
        artifact.sourcePath,
    ].map((value) => String(value || "").trim().toLowerCase()).join(" ");
    if (probe.includes("application/json") || /\.json(?:\s|$)/.test(probe)) return "json" as const;
    if (probe.includes("text/markdown") || /\.(?:md|markdown)(?:\s|$)/.test(probe)) return "markdown" as const;
    return null;
}

function artifactWorkspaceId(artifact: ArtifactDetail) {
    const metadata = artifact.metadata || {};
    return String(
        artifact.workspaceId
        || metadata.workspaceId
        || metadata.workspace_id
        || artifact.resourceRef?.workspaceId
        || "",
    ).trim();
}

function artifactBelongsToAuthority(artifact: ArtifactDetail, sessionId: string, workspaceId: string) {
    const expectedSessionId = String(sessionId || "").trim();
    const expectedWorkspaceId = String(workspaceId || "").trim();
    if (!expectedSessionId || !expectedWorkspaceId) return false;
    return String(artifact.sessionId || "").trim() === expectedSessionId
        && artifactWorkspaceId(artifact) === expectedWorkspaceId;
}

function artifactTypeLabel(artifact: ArtifactDetail, t: ReturnType<typeof useUiPrefs>["t"]) {
    const probe = `${artifact.kind || ""} ${artifact.mimeType || ""} ${artifact.title || ""}`.toLowerCase();
    if (probe.includes("image/") || /\.(?:png|jpe?g|webp|gif)(?:\s|$)/.test(probe)) return t("src.screens.artifactsscreen.type_image");
    if (probe.includes("video/") || /\.(?:mp4|webm|mov)(?:\s|$)/.test(probe)) return t("src.screens.artifactsscreen.type_video");
    if (probe.includes("audio/") || /\.(?:mp3|wav|m4a|ogg)(?:\s|$)/.test(probe)) return t("src.screens.artifactsscreen.type_audio");
    if (probe.includes("model/gltf") || /\.(?:glb|gltf)(?:\s|$)/.test(probe)) return t("src.screens.artifactsscreen.type_3d");
    if (artifactTextKind(artifact) === "markdown") return t("src.screens.artifactsscreen.type_markdown");
    if (artifactTextKind(artifact) === "json") return t("src.screens.artifactsscreen.type_json");
    return t("src.screens.artifactsscreen.type_file");
}

function artifactOriginLabel(artifact: ArtifactDetail, t: ReturnType<typeof useUiPrefs>["t"]) {
    const probe = `${artifact.origin || ""} ${artifact.kind || ""}`.toLowerCase();
    return /(upload|user_source|source)/.test(probe)
        ? t("src.screens.artifactsscreen.source_user")
        : t("src.screens.artifactsscreen.source_task");
}

function ArtifactPreview({
    artifact,
    sessionId,
    workspaceId,
    authorizedFetch,
}: {
    artifact: ArtifactDetail;
    sessionId: string;
    workspaceId: string;
    authorizedFetch: AuthorizedFetch;
}) {
    const { t } = useUiPrefs();
    const textKind = artifactTextKind(artifact);
    const [textContent, setTextContent] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [reloadToken, setReloadToken] = useState(0);
    const workspacePath = String(
        artifact.workspaceRelativePath
        || artifact.workspacePath
        || artifact.sourcePath?.replace(/^\/workspace\//, "")
        || "",
    ).trim();
    const workspaceReadAllowed = artifactBelongsToAuthority(artifact, sessionId, workspaceId);

    useEffect(() => {
        let disposed = false;
        if (!textKind) {
            setTextContent("");
            setError("");
            return () => { disposed = true; };
        }
        setLoading(true);
        setError("");
        void (async () => {
            try {
                let raw = "";
                let truncated = false;
                if (workspaceReadAllowed && workspacePath) {
                    const page = await readSessionWorkbenchFile(authorizedFetch, sessionId, workspacePath, 1, 300);
                    if (page.binary) throw new Error(t("src.screens.artifactsscreen.text_preview_binary"));
                    raw = page.content ?? (page.lines || []).map((line) => line.text).join("\n");
                    truncated = raw.length > MAX_INLINE_TEXT_CHARS || Boolean(page.hasMore);
                    raw = raw.slice(0, MAX_INLINE_TEXT_CHARS);
                } else {
                    const response = await fetchArtifactContentResponse(authorizedFetch, artifact.id, sessionId);
                    const bounded = await readBoundedResponseText(response, {
                        maxBytes: MAX_INLINE_TEXT_BYTES,
                        maxChars: MAX_INLINE_TEXT_CHARS,
                    });
                    raw = bounded.text;
                    truncated = bounded.truncated;
                }
                if (disposed) return;
                setTextContent(truncated
                    ? `${raw}\n\n${t("src.screens.artifactsscreen.text_preview_truncated")}`
                    : raw);
            } catch (reason) {
                if (!disposed) {
                    if (reason instanceof BoundedResponseTextError) {
                        setError(t(reason.code === "too_large"
                            ? "src.screens.artifactsscreen.text_preview_too_large"
                            : "src.screens.artifactsscreen.text_preview_stream_unavailable"));
                        return;
                    }
                    const message = reason instanceof Error ? reason.message : "";
                    const visibleValidationMessages = new Set([
                        t("src.screens.artifactsscreen.text_preview_binary"),
                        t("src.screens.artifactsscreen.text_preview_too_large"),
                    ]);
                    setError(visibleValidationMessages.has(message)
                        ? message
                        : t("src.screens.artifactsscreen.text_preview_load_failed"));
                }
            } finally {
                if (!disposed) setLoading(false);
            }
        })();
        return () => { disposed = true; };
    }, [artifact.id, authorizedFetch, reloadToken, sessionId, t, textKind, workspaceId, workspacePath, workspaceReadAllowed]);

    if (textKind) {
        if (loading) {
            return <ActivityIndicator color={colors.primary} style={styles.previewLoading} />;
        }
        if (error) {
            return (
                <View style={styles.inlineError}>
                    <Text selectable style={styles.inlineErrorText}>{error}</Text>
                    <Pressable accessibilityRole="button" onPress={() => setReloadToken((value) => value + 1)} style={styles.retryButton}>
                        <MaterialCommunityIcons name="refresh" size={15} color={colors.primary} />
                        <Text style={styles.retryButtonText}>{t("src.screens.artifactsscreen.retry")}</Text>
                    </Pressable>
                </View>
            );
        }
        if (textKind === "markdown") return <MarkdownRenderer content={textContent} />;
        let jsonContent = textContent;
        try {
            jsonContent = JSON.stringify(JSON.parse(textContent), null, 2);
        } catch {
            // Preserve malformed or truncated JSON as source text.
        }
        return <CodeBlock language="json" value={jsonContent} />;
    }

    const node: PhoneUiArtifactNode = {
        id: `artifact-preview:${artifact.id}`,
        kind: "artifact",
        timestamp: Date.parse(String(artifact.createdAt || "")) || 0,
        runId: artifact.runId,
        artifact,
    };
    return <MessageBlockItem node={node} />;
}

export default function ArtifactsScreen() {
    const { status, userAvatarUri, adminBaseUrl, authorizedFetch, getEngineNowMs } = useAppSession();
    const { t, locale } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const params = useLocalSearchParams<{ conversationId?: string | string[]; artifactId?: string | string[] }>();
    const conversationId = normalizeParam(params.conversationId).trim();
    const artifactId = normalizeParam(params.artifactId).trim();

    const [loading, setLoading] = useState(true);
    const [opening, setOpening] = useState(false);
    const [artifacts, setArtifacts] = useState<ArtifactDetail[]>([]);
    const [selectedArtifact, setSelectedArtifact] = useState<ArtifactDetail | null>(null);
    const [scopeWorkspaceId, setScopeWorkspaceId] = useState("");
    const [listError, setListError] = useState("");
    const [detailError, setDetailError] = useState("");
    const loadRequestRef = useRef(0);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "sessions", icon: "view-headline", onPress: () => router.push("/sessions" as Href) },
        { key: "approvals", icon: "bell-outline", onPress: () => router.push("/approvals" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        const requestId = ++loadRequestRef.current;
        setLoading(true);
        setListError("");
        setDetailError("");
        setScopeWorkspaceId("");
        const [scopeResult, listResult, detailResult] = await Promise.allSettled([
            conversationId ? getSessionScope(authorizedFetch, conversationId) : Promise.resolve(null),
            conversationId ? listArtifacts(authorizedFetch, conversationId) : Promise.resolve([]),
            artifactId && conversationId ? getArtifact(authorizedFetch, artifactId, conversationId) : Promise.resolve(null),
        ]);
        if (requestId !== loadRequestRef.current) return;

        const expectedWorkspaceId = scopeResult.status === "fulfilled"
            ? String(scopeResult.value?.workspaceId || "").trim()
            : "";
        if (!conversationId || !expectedWorkspaceId) {
            setArtifacts([]);
            setSelectedArtifact(null);
            setListError(t("src.screens.artifactsscreen.scope_binding_unavailable"));
            setLoading(false);
            return;
        }
        setScopeWorkspaceId(expectedWorkspaceId);

        const rawList = listResult.status === "fulfilled" ? [...listResult.value] : [];
        const list = rawList.filter((item) => artifactBelongsToAuthority(item, conversationId, expectedWorkspaceId));
        if (listResult.status === "rejected") {
            setListError(t("src.screens.artifactsscreen.unable_to_load_artifact_list"));
        } else if (list.length !== rawList.length) {
            setListError(t("src.screens.artifactsscreen.artifact_binding_mismatch"));
        }
        let nextSelected = list.find((item) => item.id === artifactId) || null;
        if (detailResult.status === "fulfilled" && detailResult.value) {
            if (artifactBelongsToAuthority(detailResult.value, conversationId, expectedWorkspaceId)) {
                nextSelected = detailResult.value;
                if (!list.some((item) => item.id === detailResult.value?.id)) list.unshift(detailResult.value);
            } else {
                setDetailError(t("src.screens.artifactsscreen.artifact_binding_mismatch"));
            }
        } else if (detailResult.status === "rejected") {
            setDetailError(t("src.screens.artifactsscreen.unable_to_load_artifact_details"));
        }
        setArtifacts(list);
        setSelectedArtifact(nextSelected || (!artifactId ? list[0] : null) || null);
        setLoading(false);
    }, [artifactId, authorizedFetch, conversationId, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
        return () => { loadRequestRef.current += 1; };
    }, [load, status]);

    const selectedTitle = useMemo(
        () => selectedArtifact?.title || selectedArtifact?.displayLabel || selectedArtifact?.kind || t("src.screens.artifactsscreen.artifacts"),
        [selectedArtifact, t],
    );

    const openSelectedArtifact = useCallback(async () => {
        if (!selectedArtifact) return;
        if (!artifactBelongsToAuthority(selectedArtifact, conversationId, scopeWorkspaceId)) {
            Alert.alert(
                t("src.screens.artifactsscreen.open_failed"),
                t("src.screens.artifactsscreen.artifact_binding_mismatch"),
            );
            return;
        }
        setOpening(true);
        try {
            let lastError: unknown = null;
            if (selectedArtifact.id) {
                try {
                    const response = await fetchArtifactContentResponse(authorizedFetch, selectedArtifact.id, conversationId);
                    const cached = await saveResponseToCache(response, {
                        prefix: `artifact-${selectedArtifact.id}`,
                    });
                    const opened = await openCachedFile(cached.uri);
                    if (!opened) {
                        Alert.alert(t("src.screens.artifactsscreen.artifact_cached"), `${t("src.screens.artifactsscreen.file_saved_to")}：${cached.uri}`);
                    }
                    return;
                } catch (reason) {
                    lastError = reason;
                }
            }

            const resourceRef = coerceAdminResourceRef(selectedArtifact.resourceRef || null);
            const directUrl = resolveAdminResourceUrl("phone", adminBaseUrl, resourceRef)
                || selectedArtifact.externalUrl
                || selectedArtifact.previewUrl;
            if (directUrl && resourceRef?.kind === "external_url") {
                await Linking.openURL(normalizeRenderableWorkspaceUrl(adminBaseUrl, directUrl));
                return;
            }

            throw lastError || new Error(t("src.screens.artifactsscreen.this_artifact_does_not_expose_any_openable_content_url"));
        } catch {
            Alert.alert(
                t("src.screens.artifactsscreen.open_failed"),
                t("src.screens.artifactsscreen.unable_to_open_this_artifact"),
            );
        } finally {
            setOpening(false);
        }
    }, [adminBaseUrl, authorizedFetch, conversationId, scopeWorkspaceId, selectedArtifact, t]);

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.artifactsscreen.loading_artifacts")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} userImageUri={userAvatarUri || undefined} onBrandPress={() => void goHomeToChat()} />

                {loading ? (
                    <LoadingScreen label={t("src.screens.artifactsscreen.syncing_artifacts")} />
                ) : (
                    <ScrollView contentContainerStyle={styles.content}>
                        <GlassCard>
                            <View style={styles.sectionHeader}>
                                <Text style={styles.sectionTitle}>{selectedTitle}</Text>
                                <Text style={styles.sectionMeta}>{t("src.screens.artifactsscreen.items_count", { count: artifacts.length })}</Text>
                            </View>
                            {listError ? (
                                <View style={styles.inlineError}>
                                    <Text selectable style={styles.inlineErrorText}>{listError}</Text>
                                    <Pressable accessibilityRole="button" onPress={() => void load()} style={styles.retryButton}>
                                        <MaterialCommunityIcons name="refresh" size={15} color={colors.primary} />
                                        <Text style={styles.retryButtonText}>{t("src.screens.artifactsscreen.retry")}</Text>
                                    </Pressable>
                                </View>
                            ) : null}
                            {artifacts.length === 0 ? (
                                <Text style={styles.emptyText}>{t("src.screens.artifactsscreen.this_conversation_has_no_artifacts_yet_new_ones_will_appear_here_once_the_supervisor_generates_them")}</Text>
                            ) : (
                                artifacts.map((artifact) => {
                                    const active = selectedArtifact?.id === artifact.id;
                                    return (
                                        <Pressable
                                            key={artifact.id}
                                            style={[styles.itemCard, active && styles.itemCardActive]}
                                            onPress={() => setSelectedArtifact(artifact)}
                                        >
                                            <View style={[styles.kindDot, active && styles.kindDotActive]} />
                                            <View style={styles.itemBody}>
                                                <Text style={styles.itemTitle} numberOfLines={1}>
                                                    {artifact.title || artifact.displayLabel || artifactTypeLabel(artifact, t)}
                                                </Text>
                                                <Text style={styles.itemSubtitle} numberOfLines={1}>
                                                    {artifactTypeLabel(artifact, t)} · {artifactOriginLabel(artifact, t)}
                                                </Text>
                                            </View>
                                            <Text style={styles.itemTime}>
                                                {formatRelativeTime(artifact.createdAt, locale, getEngineNowMs())}
                                            </Text>
                                        </Pressable>
                                    );
                                })
                            )}
                        </GlassCard>

                        <GlassCard>
                            <View style={styles.sectionHeader}>
                                <Text style={styles.sectionTitle}>{t("src.screens.artifactsscreen.details")}</Text>
                                <Pressable
                                    style={[styles.primaryButton, opening && styles.disabled]}
                                    onPress={() => void openSelectedArtifact()}
                                    disabled={!selectedArtifact || opening}
                                >
                                    <MaterialCommunityIcons name={opening ? "loading" : "open-in-new"} size={16} color="#FFFFFF" />
                                    <Text style={styles.primaryButtonText}>{opening ? t("src.screens.artifactsscreen.opening") : t("src.screens.artifactsscreen.open_content")}</Text>
                                </Pressable>
                            </View>
                            {detailError ? (
                                <View style={styles.inlineError}>
                                    <Text selectable style={styles.inlineErrorText}>{detailError}</Text>
                                    <Pressable accessibilityRole="button" onPress={() => void load()} style={styles.retryButton}>
                                        <MaterialCommunityIcons name="refresh" size={15} color={colors.primary} />
                                        <Text style={styles.retryButtonText}>{t("src.screens.artifactsscreen.retry")}</Text>
                                    </Pressable>
                                </View>
                            ) : null}
                            {!selectedArtifact ? (
                                <Text style={styles.emptyText}>{t("src.screens.artifactsscreen.select_an_artifact_above_to_inspect_its_details")}</Text>
                            ) : (
                                <View style={styles.detailList}>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("src.screens.artifactsscreen.title")}</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.title || selectedArtifact.displayLabel || t("src.screens.artifactsscreen.untitled_artifact")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("src.screens.artifactsscreen.type")}</Text>
                                        <Text style={styles.detailValue}>{artifactTypeLabel(selectedArtifact, t)}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("src.screens.artifactsscreen.source")}</Text>
                                        <Text style={styles.detailValue}>{artifactOriginLabel(selectedArtifact, t)}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("src.screens.artifactsscreen.created")}</Text>
                                        <Text style={styles.detailValue}>{formatClock(selectedArtifact.createdAt, locale) || t("src.screens.artifactsscreen.unknown")}</Text>
                                    </View>
                                </View>
                            )}
                        </GlassCard>
                        {selectedArtifact ? (
                            <GlassCard>
                                <View style={styles.sectionHeader}>
                                    <Text style={styles.sectionTitle}>{t("src.screens.artifactsscreen.preview")}</Text>
                                </View>
                                <ArtifactPreview
                                    artifact={selectedArtifact}
                                    sessionId={conversationId}
                                    workspaceId={scopeWorkspaceId}
                                    authorizedFetch={authorizedFetch}
                                />
                            </GlassCard>
                        ) : null}
                    </ScrollView>
                )}
            </SafeAreaView>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: { flex: 1 },
    safeArea: { flex: 1 },
    content: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.xl,
        gap: spacing.md,
    },
    sectionHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: spacing.md,
        gap: spacing.sm,
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 16,
        fontWeight: "900",
    },
    sectionMeta: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "800",
    },
    emptyText: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 22,
    },
    itemCard: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 12,
        paddingVertical: 12,
        marginBottom: spacing.sm,
    },
    itemCardActive: {
        borderColor: "rgba(124,58,237,0.26)",
        backgroundColor: colors.primarySoft,
    },
    kindDot: {
        width: 10,
        height: 10,
        borderRadius: 5,
        backgroundColor: colors.textSoft,
    },
    kindDotActive: {
        backgroundColor: colors.primary,
    },
    itemBody: {
        flex: 1,
        gap: 4,
    },
    itemTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    itemSubtitle: {
        color: colors.textMuted,
        fontSize: 12,
    },
    itemTime: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "700",
    },
    detailList: {
        gap: spacing.sm,
    },
    detailRow: {
        gap: 4,
    },
    detailLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 0.8,
        textTransform: "uppercase",
    },
    detailValue: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 21,
    },
    primaryButton: {
        minHeight: 38,
        borderRadius: radii.md,
        backgroundColor: colors.primary,
        paddingHorizontal: 12,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontSize: 13,
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
    previewLoading: {
        paddingVertical: spacing.xl,
    },
    inlineError: {
        borderWidth: 1,
        borderColor: "rgba(220,38,38,0.22)",
        borderRadius: radii.md,
        backgroundColor: "rgba(254,226,226,0.62)",
        paddingHorizontal: 12,
        paddingVertical: 10,
        gap: 8,
        marginBottom: spacing.sm,
    },
    inlineErrorText: {
        color: colors.danger,
        fontSize: 12,
        lineHeight: 18,
    },
    retryButton: {
        minHeight: 34,
        alignSelf: "flex-start",
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 10,
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: "rgba(124,58,237,0.25)",
        backgroundColor: colors.surface,
    },
    retryButtonText: {
        color: colors.primary,
        fontSize: 12,
        fontWeight: "800",
    },
});
