import { useEffect, useRef, useState } from "react";
import { Alert, Pressable, Text, TextInput, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { canReviseConversationMessage, createMessageRevisionDraft, describeRecoveryDescendants, messageRevisionVersion, readTranscriptIdentity, type ConversationMutationResult, type MessageRevisionDraft, type RecoveryMessage } from "@v8/session-realtime";
import { usePhoneDraftField } from "@/src/hooks/use-phone-draft";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export type ConversationRecoveryProps = {
    sessionId: string; draftKey: string; transcriptRevision: number; busy: boolean;
    onCommitted: (result: ConversationMutationResult) => Promise<void>;
};
export function ConversationRecoveryActions({ message, hasDescendants, laterTurnCount, turnEnd, recovery }: {
    message: RecoveryMessage; hasDescendants: boolean; laterTurnCount: number; turnEnd: boolean; recovery: ConversationRecoveryProps;
}) {
    const { t, colors } = useUiPrefs();
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const { authorizedFetch, setActiveConversationId } = useAppSession();
    const [draft, setDraft] = usePhoneDraftField<MessageRevisionDraft | null>(recovery.draftKey, `revision:${message.id}`, null);
    const [open, setOpen] = useState(false), [saving, setSaving] = useState(false);
    const [online, setOnline] = useState(true), [error, setError] = useState("");
    const allowed = canReviseConversationMessage(message, recovery.busy);
    const disabled = !allowed || saving || !online;
    const [topology, setTopology] = useState({ hasDescendants, laterTurnCount });
    const freshRead = async () => {
        const response = await authorizedFetch(`/api/client/conversations/${encodeURIComponent(recovery.sessionId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error("offline");
        setOnline(true);
        return response.json();
    };
    const beginEdit = async () => {
        if (!draft) setDraft(createMessageRevisionDraft(message, recovery.transcriptRevision));
        setOpen(true); setSaving(true);
        try { setTopology(describeRecoveryDescendants(await freshRead(), message.id)); }
        catch { setOnline(false); setError(t("conversationRecovery.offline")); }
        finally { setSaving(false); }
    };
    const confirmTruncate = async () => {
        setSaving(true);
        try {
            const current = await freshRead();
            const descendants = describeRecoveryDescendants(current, message.id);
            setTopology(descendants);
            if (draft?.expectedTranscriptRevision !== readTranscriptIdentity(current).transcriptRevision) {
                await recovery.onCommitted({ sessionId: recovery.sessionId, ...readTranscriptIdentity(current) });
                setError(t("conversationRecovery.conflict"));
            } else Alert.alert(t("conversationRecovery.truncate"), t("conversationRecovery.truncateConfirm", { count: descendants.laterTurnCount }), [
                { text: t("conversationRecovery.cancel"), style: "cancel" },
                { text: t("conversationRecovery.truncate"), style: "destructive", onPress: () => void submit("truncate") },
            ]);
        } catch { setOnline(false); setError(t("conversationRecovery.offline")); }
        finally { setSaving(false); }
    };
    const submit = async (mode: "branch" | "save" | "truncate", edit = true) => {
        if (disabled || (edit && !draft)) return;
        setSaving(true); setError("");
        const base = edit && draft ? draft : createMessageRevisionDraft(message, recovery.transcriptRevision);
        try {
            const current = await freshRead();
            if (readTranscriptIdentity(current).transcriptRevision !== base.expectedTranscriptRevision) {
                await recovery.onCommitted({ sessionId: recovery.sessionId, ...readTranscriptIdentity(current) });
                throw new Error("conflict");
            }
            const descendants = describeRecoveryDescendants(current, message.id);
            setTopology(descendants);
            if (mode === "save" && descendants.hasDescendants) mode = "branch";
            const branch = mode === "branch";
            const body = branch ? { turnId: message.turnId, expectedMessageVersion: edit ? base.expectedMessageVersion : messageRevisionVersion(message),
                expectedTranscriptRevision: base.expectedTranscriptRevision, ...(edit ? { messageId: message.id, content: base.content } : {}) }
                : { content: base.content, expectedMessageVersion: base.expectedMessageVersion, expectedTranscriptRevision: base.expectedTranscriptRevision, tailPolicy: mode === "truncate" ? "truncate" : "reject" };
            const response = await authorizedFetch(`/api/client/conversations/${encodeURIComponent(recovery.sessionId)}/${branch ? "branches" : `messages/${encodeURIComponent(message.id)}`}`, {
                method: branch ? "POST" : "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            });
            if (response.status === 409) {
                const latest = await freshRead();
                await recovery.onCommitted({ sessionId: recovery.sessionId, ...readTranscriptIdentity(latest) });
                throw new Error("conflict");
            }
            if (!response.ok) throw new Error(response.status >= 502 ? "offline" : "failed");
            const result = await response.json() as ConversationMutationResult;
            if (edit) setDraft(null);
            setOpen(false);
            await recovery.onCommitted(result);
            if (branch && mounted.current) await setActiveConversationId(result.newSessionId || result.sessionId);
        } catch (failure) {
            const code = failure instanceof Error ? failure.message : "failed";
            if (code === "offline" || /network|fetch/i.test(code)) setOnline(false);
            setError(t(`conversationRecovery.${code === "conflict" ? "conflict" : code === "failed" ? "failed" : "offline"}`));
        } finally { setSaving(false); }
    };
    const button = (key: string, onPress: () => void, blocked = false, icon?: "pencil" | "source-branch" | "content-save") => <Pressable accessibilityRole="button" accessibilityLabel={t(`conversationRecovery.${key}`)} disabled={blocked} onPress={onPress}
        style={{ minHeight: 40, paddingHorizontal: 10, flexDirection: "row", gap: 5, alignItems: "center", opacity: blocked ? 0.4 : 1 }}>
        {icon && <MaterialCommunityIcons name={icon} size={17} color={colors.text} />}<Text style={{ color: colors.text }}>{t(`conversationRecovery.${key}`)}</Text>
    </Pressable>;
    if (!allowed && !draft) return message.editedBy === "user" ? <Text style={{ color: colors.textMuted }}>{t("conversationRecovery.edited")}</Text> : null;
    return <View style={{ gap: 8, marginBottom: 12 }}>
        <View style={{ flexDirection: "row", justifyContent: "flex-end", flexWrap: "wrap" }}>
            {message.editedBy === "user" && <Text style={{ color: colors.textMuted }}>{t("conversationRecovery.edited")}</Text>}
            {button(draft ? "resumeDraft" : "edit", () => void beginEdit(), saving || !allowed, "pencil")}
            {turnEnd && button("branch", () => void submit("branch", false), disabled, "source-branch")}
        </View>
        {open && draft && <View style={{ gap: 8 }}>
            <TextInput accessibilityLabel={t("conversationRecovery.edit")} multiline value={draft.content}
                onChangeText={(content) => setDraft({ ...draft, content })} style={{ minHeight: 130, maxHeight: 320, borderWidth: 1, borderColor: colors.border, borderRadius: 8, padding: 12, color: colors.text, textAlignVertical: "top" }} />
            <View style={{ flexDirection: "row", justifyContent: "flex-end", flexWrap: "wrap" }}>
                {button("closeDraft", () => setOpen(false))}
                {button("discard", () => { setDraft(null); setOpen(false); setError(""); }, saving)}
                {topology.hasDescendants && button("truncate", () => void confirmTruncate(), disabled || !draft.content.trim())}
                {button(topology.hasDescendants ? "branchEdit" : "save", () => void submit(topology.hasDescendants ? "branch" : "save"), disabled || !draft.content.trim(), "content-save")}
            </View>
            {error ? <><Text accessibilityRole="alert" style={{ color: colors.text }}>{error}</Text><Text selectable style={{ color: colors.textMuted }}>{message.content}</Text></> : null}
            {error && draft.expectedTranscriptRevision !== recovery.transcriptRevision && button("useCurrentVersion", () => { setDraft({ ...draft, expectedMessageVersion: messageRevisionVersion(message), expectedTranscriptRevision: recovery.transcriptRevision }); setError(""); })}
        </View>}
        {!online && button("reconnect", () => { void freshRead().then((payload) => recovery.onCommitted({ sessionId: recovery.sessionId, ...readTranscriptIdentity(payload) })).catch(() => setOnline(false)); })}
        {!open && error ? <Text accessibilityRole="alert" style={{ color: colors.text }}>{error}</Text> : null}
    </View>;
}
