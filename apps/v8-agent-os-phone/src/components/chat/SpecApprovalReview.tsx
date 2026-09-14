import { useCallback, useEffect, useRef, useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { Button } from "@/src/components/ui/button";
import { usePhoneDraftField, usePhoneDraftStatus } from "@/src/hooks/use-phone-draft";
import { approvePendingItem, getSpecDetail, listPendingApprovals, refreshSpecApprovalReview } from "@/src/lib/phone-api";
import { phoneDrafts } from "@/src/lib/phone-drafts";
import { isSpecStageApproval, specReviewDraftKey } from "@/src/lib/spec-approval-review";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import type { PendingApproval, SpecStageContent } from "@/src/types/admin";

type Props = {
    approvalId: string;
    authorityKey: string;
    workspacePath: string;
    specId: string;
    stage: string;
    authorizedFetch: (path: string, init?: RequestInit) => Promise<Response>;
};

export function SpecApprovalReview(props: Props) {
    const { colors, t } = useUiPrefs();
    const draftKey = specReviewDraftKey(props.authorityKey, props.approvalId);
    const [comment, setComment] = usePhoneDraftField(draftKey, "comment", "");
    const draft = usePhoneDraftStatus(draftKey);
    const [approval, setApproval] = useState<PendingApproval | null>(null);
    const [document, setDocument] = useState<SpecStageContent | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [conflict, setConflict] = useState(false);
    const [done, setDone] = useState(false);
    const sequence = useRef(0);
    const inFlight = useRef(false);
    const identity = `${props.authorityKey}:${props.approvalId}`;
    const activeIdentity = useRef(identity);
    activeIdentity.current = identity;

    const readDocument = useCallback(async (card: PendingApproval) => {
        const request = card.request || {};
        const specId = String(request.specId || request.spec_id || props.specId);
        const workspace = String(request.workspacePath || request.workspace_path || props.workspacePath);
        const stage = String(request.stage || request.specStage || request.spec_stage || props.stage);
        if (!specId || !workspace || !stage) throw new Error(t("phone.specReview.unavailable"));
        const detail = await getSpecDetail(props.authorizedFetch, specId, workspace);
        const current = detail.stages?.[stage];
        if (!current || typeof current.content !== "string" || !current.documentSha256 || !current.documentPath) {
            throw new Error(t("phone.specReview.unavailable"));
        }
        return current;
    }, [props.authorizedFetch, props.specId, props.workspacePath, props.stage, t]);

    const showError = (failure: unknown) => {
        const code = (failure as { code?: string })?.code;
        if (code === "spec_approval_document_changed" || code === "spec_approval_version_required") {
            setConflict(true);
            setError(t("phone.specReview.changed"));
        } else {
            setError(failure instanceof Error ? failure.message : t("phone.specReview.refreshFailed"));
        }
    };

    const load = useCallback(async () => {
        if (inFlight.current) return;
        inFlight.current = true;
        const operation = ++sequence.current;
        setBusy(true); setDocument(null); setError("");
        try {
            await phoneDrafts.hydrate(draftKey);
            const saved = phoneDrafts.get(draftKey).values.approval as PendingApproval | undefined;
            const id = saved?.id || saved?.approval_id || props.approvalId;
            const items = await listPendingApprovals(props.authorizedFetch);
            // A refresh with a lost response may already have cancelled the old
            // card. Its same ID/hash refresh can recover the immutable successor.
            const card = items.find(item => (item.id || item.approval_id) === id) || saved || {
                id: props.approvalId, approval_kind: "spec_stage_approval",
                request: { workspacePath: props.workspacePath, specId: props.specId, stage: props.stage },
            };
            if (!isSpecStageApproval(card)) throw new Error(t("phone.specReview.unavailable"));
            const current = await readDocument(card);
            if (operation !== sequence.current || activeIdentity.current !== identity) return;
            setApproval(card); setDocument(current); setConflict(false);
            setDone(card.status === "approved" || card.status === "rejected");
        } catch (failure) {
            if (operation === sequence.current && activeIdentity.current === identity) {
                setError(failure instanceof Error ? failure.message : t("phone.specReview.unavailable"));
            }
        } finally {
            if (operation === sequence.current) { inFlight.current = false; setBusy(false); }
        }
    }, [draftKey, props.approvalId, props.authorizedFetch, props.specId, props.stage, props.workspacePath, readDocument, identity, t]);

    useEffect(() => {
        void load();
        return () => { sequence.current++; inFlight.current = false; };
    }, [load]);

    const complete = Boolean(document && document.truncated === false);
    const matches = Boolean(complete && approval?.request?.documentSha256 === document?.documentSha256
        && approval?.request?.documentPath === document?.documentPath && !conflict);
    const act = async (action: "refresh" | "approve" | "reject") => {
        if (inFlight.current || !approval || done || (action !== "reject" && !complete)
            || (action === "approve" && !matches)) return;
        inFlight.current = true;
        const operation = ++sequence.current;
        const cardId = String(approval.id || approval.approval_id || "");
        if (!cardId) { inFlight.current = false; return; }
        const current = () => operation === sequence.current && activeIdentity.current === identity;
        setBusy(true); setError("");
        try {
            if (action === "refresh") {
                const result = await refreshSpecApprovalReview(props.authorizedFetch, cardId, document!.documentSha256!);
                if (!current()) return;
                if (result.replacesApprovalId !== cardId || !isSpecStageApproval(result.approval)
                    || !(result.approval.id || result.approval.approval_id)) throw new Error(t("phone.specReview.refreshFailed"));
                setApproval(result.approval); setDocument(null);
                phoneDrafts.set(draftKey, "approval", result.approval);
                await phoneDrafts.flush(draftKey);
                const nextDocument = await readDocument(result.approval);
                if (!current()) return;
                setDocument(nextDocument); setConflict(false);
                setDone(result.approval.status === "approved" || result.approval.status === "rejected");
            } else {
                await approvePendingItem(props.authorizedFetch, cardId, comment, action === "approve", document?.documentSha256);
                if (!current()) return;
                setDone(true);
                phoneDrafts.set(draftKey, "approval", { ...approval, status: action === "approve" ? "approved" : "rejected" });
                setComment(previous => previous === comment ? "" : previous);
            }
        } catch (failure) {
            if (current()) showError(failure);
        } finally {
            if (current()) { inFlight.current = false; setBusy(false); }
        }
    };

    return (
        <ScrollView contentContainerStyle={styles.content}>
            <Text style={[styles.title, { color: colors.text }]}>{t("src.screens.specapprovalscreen.title")}</Text>
            {document ? <>
                <Text selectable style={{ color: colors.textMuted }}>{document.documentPath}</Text>
                <Text selectable style={[styles.document, { color: colors.text }]}>{document.content}</Text>
                {document.truncated !== false ? <Text style={{ color: colors.warning }}>{t("src.screens.specapprovalscreen.truncated_hint")}</Text> : null}
            </> : <Text style={{ color: colors.textMuted }}>{t("phone.specReview.unavailable")}</Text>}
            {complete && !matches && !done ? <Text style={{ color: colors.warning }}>{t("phone.specReview.changed")}</Text> : null}
            {error || draft.error ? <Text accessibilityRole="alert" style={{ color: colors.danger }}>{error || draft.error}</Text> : null}
            {done ? <Text style={{ color: colors.text }}>{t("phone.specReview.resolved")}</Text> : <>
                <TextInput accessibilityLabel={t("src.screens.specapprovalscreen.comment_label")} value={comment} onChangeText={setComment}
                    multiline style={[styles.input, { color: colors.text, borderColor: colors.border }]}
                    placeholder={t("src.screens.specapprovalscreen.comment_placeholder")} placeholderTextColor={colors.textMuted} />
                <View style={styles.actions}>
                    <Button variant="outline" disabled={busy} onPress={() => void load()}>{t("phone.specReview.reload")}</Button>
                    <Button disabled={busy || !complete || matches} onPress={() => void act("refresh")}>{t("phone.specReview.refresh")}</Button>
                    <Button disabled={busy || !matches || Boolean(draft.error)} onPress={() => void act("approve")}>{t("src.screens.specapprovalscreen.approve_next")}</Button>
                    <Button variant="ghost" disabled={busy || !approval} onPress={() => void act("reject")}>{t("src.components.chat.approvalpromptcard.reject")}</Button>
                </View>
            </>}
        </ScrollView>
    );
}

const styles = StyleSheet.create({
    content: { padding: 20, gap: 16 },
    title: { fontSize: 22, fontWeight: "700" },
    document: { fontSize: 14, lineHeight: 22 },
    input: { borderWidth: 1, borderRadius: 12, padding: 12, minHeight: 90, textAlignVertical: "top" },
    actions: { gap: 10 },
});
