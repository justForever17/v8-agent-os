"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { GitBranch, Pencil, Save, X } from "lucide-react";
import { canReviseConversationMessage, createMessageRevisionDraft, describeRecoveryDescendants, messageRevisionVersion, readTranscriptIdentity, type ConversationMutationResult, type MessageRevisionDraft, type RecoveryMessage } from "@v8/session-realtime";
import { useDraftField } from "@/hooks/use-composer-draft";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export type ConversationRecoveryProps = {
    sessionId: string; draftKey: string; transcriptRevision: number; busy: boolean;
    onCommitted: (result: ConversationMutationResult) => Promise<void>;
};
export function ConversationRecoveryActions({ message, hasDescendants, laterTurnCount, turnEnd, recovery, renderActionRow, compact = false }: {
    message: RecoveryMessage; hasDescendants: boolean; laterTurnCount: number; turnEnd: boolean; recovery: ConversationRecoveryProps;
    renderActionRow?: (actions: ReactNode) => ReactNode;
    compact?: boolean;
}) {
    const t = useT(), router = useRouter();
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const [draft, setDraft] = useDraftField<MessageRevisionDraft | null>(recovery.draftKey, `revision:${message.id}`, null);
    const [open, setOpen] = useState(false), [saving, setSaving] = useState(false), [confirm, setConfirm] = useState(false);
    const [online, setOnline] = useState(true), [error, setError] = useState("");
    useEffect(() => { const sync = () => setOnline(navigator.onLine); sync(); window.addEventListener("online", sync); window.addEventListener("offline", sync); return () => { window.removeEventListener("online", sync); window.removeEventListener("offline", sync); }; }, []);
    const allowed = canReviseConversationMessage(message, recovery.busy);
    const disabled = !allowed || saving || !online;
    const [topology, setTopology] = useState({ hasDescendants, laterTurnCount });
    const freshRead = async () => {
        const response = await fetch(`/api/conversations/${encodeURIComponent(recovery.sessionId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error("offline");
        return response.json();
    };
    const beginEdit = async () => {
        if (!draft) setDraft(createMessageRevisionDraft(message, recovery.transcriptRevision));
        setOpen(true); setSaving(true);
        try { setTopology(describeRecoveryDescendants(await freshRead(), message.id)); }
        catch { setError(t("conversationRecovery.offline")); }
        finally { setSaving(false); }
    };
    const confirmTruncate = async () => {
        setSaving(true);
        try {
            const current = await freshRead();
            setTopology(describeRecoveryDescendants(current, message.id));
            if (draft?.expectedTranscriptRevision !== readTranscriptIdentity(current).transcriptRevision) {
                await recovery.onCommitted({ sessionId: recovery.sessionId, ...readTranscriptIdentity(current) });
                setError(t("conversationRecovery.conflict"));
            } else setConfirm(true);
        } catch { setError(t("conversationRecovery.offline")); }
        finally { setSaving(false); }
    };
    const submit = async (mode: "branch" | "save" | "truncate", edit = true) => {
        if (disabled || (edit && !draft)) return;
        setSaving(true); setError("");
        const base = edit && draft ? draft : createMessageRevisionDraft(message, recovery.transcriptRevision);
        try {
            // A fresh read is required after reconnect; retain the draft's original CAS values.
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
            const response = await fetch(`/api/conversations/${encodeURIComponent(recovery.sessionId)}/${branch ? "branches" : `messages/${encodeURIComponent(message.id)}`}`, {
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
            setOpen(false); setConfirm(false);
            await recovery.onCommitted(result);
            if (branch && mounted.current) router.push(`/chat?id=${encodeURIComponent(result.newSessionId || result.sessionId)}`);
        } catch (failure) {
            const code = failure instanceof Error ? failure.message : "failed";
            setError(t(`conversationRecovery.${["conflict", "offline"].includes(code) ? code : "failed"}`));
        } finally { setSaving(false); }
    };
    const recoveryActions = (allowed || draft) ? <>
        <Button variant="ghost" size="icon" className="h-6 w-6 rounded-none border-0 bg-transparent p-0 text-muted-foreground/70 shadow-none hover:bg-transparent hover:text-foreground" disabled={saving || !allowed} aria-label={draft ? t("conversationRecovery.resumeDraft") : t("conversationRecovery.edit")} title={draft ? t("conversationRecovery.resumeDraft") : t("conversationRecovery.edit")}
            onClick={() => void beginEdit()}>
            <Pencil className="h-3.5 w-3.5" />
        </Button>
        {turnEnd && <Button variant="ghost" size="icon" className="h-6 w-6 rounded-none border-0 bg-transparent p-0 text-muted-foreground/70 shadow-none hover:bg-transparent hover:text-foreground" disabled={disabled} aria-label={t("conversationRecovery.branch")} title={t("conversationRecovery.branch")} onClick={() => void submit("branch", false)}><GitBranch className="h-3.5 w-3.5" /></Button>}
    </> : null;
    if (!allowed && !draft && !renderActionRow) return message.editedBy === "user" ? <p className="text-xs text-muted-foreground">{t("conversationRecovery.edited")}</p> : null;
    return <div className={compact ? "space-y-2" : "mt-2 space-y-2"} data-testid="conversation-recovery">
        {renderActionRow ? (
            <>
                {message.editedBy === "user" && <span className="mr-2 text-xs text-muted-foreground">{t("conversationRecovery.edited")}</span>}
                {renderActionRow(recoveryActions)}
            </>
        ) : (
            <div className="flex min-h-8 items-center justify-end gap-2">
                {message.editedBy === "user" && <span className="mr-auto text-xs text-muted-foreground">{t("conversationRecovery.edited")}</span>}
                <Button variant="ghost" size="sm" disabled={saving || !allowed} aria-label={t("conversationRecovery.edit")} title={t("conversationRecovery.edit")}
                    onClick={() => void beginEdit()}>
                    <Pencil className="h-4 w-4" />{draft ? t("conversationRecovery.resumeDraft") : t("conversationRecovery.edit")}
                </Button>
                {turnEnd && <Button variant="ghost" size="sm" disabled={disabled} aria-label={t("conversationRecovery.branch")} onClick={() => void submit("branch", false)}><GitBranch className="h-4 w-4" />{t("conversationRecovery.branch")}</Button>}
            </div>
        )}
        {open && draft && <div className="space-y-2">
            <textarea aria-label={t("conversationRecovery.edit")} className="min-h-32 max-h-96 w-full resize-y rounded-md border bg-background p-3 font-mono text-sm" value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} />
            <div className="flex flex-wrap justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setOpen(false)}><X className="h-4 w-4" />{t("conversationRecovery.closeDraft")}</Button>
                <Button variant="ghost" size="sm" disabled={saving} onClick={() => { setDraft(null); setOpen(false); setError(""); }}>{t("conversationRecovery.discard")}</Button>
                {topology.hasDescendants && <Button variant="outline" size="sm" disabled={disabled || !draft.content.trim()} onClick={() => void confirmTruncate()}>{t("conversationRecovery.truncate")}</Button>}
                <Button size="sm" disabled={disabled || !draft.content.trim()} onClick={() => void submit(topology.hasDescendants ? "branch" : "save")}><Save className="h-4 w-4" />{t(topology.hasDescendants ? "conversationRecovery.branchEdit" : "conversationRecovery.save")}</Button>
            </div>
            {error && <div role="alert" className="text-sm text-destructive">{error}<pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-foreground">{message.content}</pre></div>}
            {error && draft.expectedTranscriptRevision !== recovery.transcriptRevision && <Button variant="outline" size="sm" onClick={() => { setDraft({ ...draft, expectedMessageVersion: messageRevisionVersion(message), expectedTranscriptRevision: recovery.transcriptRevision }); setError(""); }}>{t("conversationRecovery.useCurrentVersion")}</Button>}
        </div>}
        {!online && <p role="status" className="text-sm text-muted-foreground">{t("conversationRecovery.offline")}</p>}
        {!open && error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>{t("conversationRecovery.truncate")}</DialogTitle><DialogDescription>{t("conversationRecovery.truncateConfirm", { count: topology.laterTurnCount })}</DialogDescription></DialogHeader>
            <DialogFooter><Button variant="outline" onClick={() => setConfirm(false)}>{t("conversationRecovery.cancel")}</Button><Button variant="destructive" disabled={disabled} onClick={() => void submit("truncate")}>{t("conversationRecovery.truncate")}</Button></DialogFooter>
        </DialogContent></Dialog>
    </div>;
}
