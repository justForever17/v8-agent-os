"use client";

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useLocale, useT } from "@/components/providers/LocaleProvider";

type Delivery = {
    deliveryId: string;
    definitionId: string;
    definitionName: string;
    kind: "cron" | "hook";
    phase: string;
    ownership: "system" | "user" | "unresolved";
    createdAt: string;
    updatedAt: string;
};
type DeliveryPage = { items: Delivery[]; limit: number; hasMore: boolean; nextCursor: string | null };
type Outcome = "completed" | "failed";

const needsReview = (item: Delivery) => item.phase === "unknown" && (item.ownership === "system" || item.ownership === "unresolved");

export function AutomationDeliveryReview() {
    const t = useT();
    const { locale } = useLocale();
    const fieldId = useId();
    const [items, setItems] = useState<Delivery[]>([]);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState("");
    const [selected, setSelected] = useState<Delivery | null>(null);
    const [outcome, setOutcome] = useState<Outcome | "">("");
    const [observation, setObservation] = useState("");
    const [reference, setReference] = useState("");
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [saveError, setSaveError] = useState("");
    const [refreshRequired, setRefreshRequired] = useState(false);
    const readRequest = useRef<AbortController | null>(null);
    const submitting = useRef(false);

    const load = useCallback(async (after?: string) => {
        readRequest.current?.abort();
        const request = new AbortController();
        readRequest.current = request;
        setLoading(true);
        setLoadError("");
        try {
            const query = new URLSearchParams({ limit: "25", ownership: "system,unresolved" });
            if (after) query.set("after", after);
            const response = await fetch(`/api/automation/deliveries?${query}`, { signal: AbortSignal.any([request.signal, AbortSignal.timeout(15_000)]), cache: "no-store" });
            if (!response.ok) {
                if (!request.signal.aborted) setLoadError(response.status === 403 ? t("automation.reconciliation.forbidden") : t("automation.reconciliation.loadError"));
                return null;
            }
            const page = await response.json() as DeliveryPage;
            if (!Array.isArray(page.items) || (page.hasMore && (typeof page.nextCursor !== "string" || !page.nextCursor))) throw new Error("invalid list receipt");
            if (request.signal.aborted) return null;
            const visible = page.items.filter(needsReview);
            setItems(previous => after ? [...new Map([...previous, ...visible].map(item => [item.deliveryId, item])).values()] : visible);
            setNextCursor(page.hasMore ? page.nextCursor : null);
            return visible;
        } catch {
            if (!request.signal.aborted) setLoadError(t("automation.reconciliation.loadError"));
            return null;
        } finally {
            if (!request.signal.aborted) setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        void load();
        return () => readRequest.current?.abort();
    }, [load]);

    useEffect(() => {
        if (!saved) return;
        const timer = window.setTimeout(() => setSaved(false), 4000);
        return () => window.clearTimeout(timer);
    }, [saved]);

    const open = (item: Delivery) => {
        if (!needsReview(item) || submitting.current) return;
        setSelected(item);
        setOutcome("");
        setObservation("");
        setReference("");
        setSaveError("");
        setRefreshRequired(false);
        setSaved(false);
    };

    const refreshReview = async () => {
        const refreshed = await load();
        if (!refreshed) return;
        const current = refreshed.find(item => item.deliveryId === selected?.deliveryId);
        setRefreshRequired(!current);
        setSaveError(current ? "" : t("automation.reconciliation.stale"));
    };

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        if (!selected || !needsReview(selected) || !outcome || !observation.trim() || refreshRequired || submitting.current) return;
        submitting.current = true;
        setSaving(true);
        setSaveError("");
        const targetId = selected.deliveryId;
        try {
            const response = await fetch(`/api/automation/deliveries/${encodeURIComponent(targetId)}/reconcile`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: AbortSignal.timeout(20_000),
                body: JSON.stringify({ outcome, evidence: { observation: observation.trim(), ...(reference.trim() ? { reference: reference.trim() } : {}) } }),
            });
            if (!response.ok) {
                const key = response.status === 409 || response.status === 404 ? "conflict"
                    : response.status === 403 || response.status === 401 ? "forbidden"
                        : response.status === 422 ? "invalid" : "saveError";
                setRefreshRequired(response.status !== 422);
                setSaveError(t(`automation.reconciliation.${key}`));
                return;
            }
            const receipt = await response.json();
            if (receipt.status !== "success" || receipt.deliveryId !== targetId || receipt.phase !== outcome) throw new Error("unconfirmed receipt");
            setItems(previous => previous.filter(item => item.deliveryId !== targetId));
            setSelected(null);
            setSaved(true);
            await load();
        } catch {
            setRefreshRequired(true);
            setSaveError(t("automation.reconciliation.saveError"));
        } finally {
            submitting.current = false;
            setSaving(false);
        }
    };

    if (!items.length && !loadError && !selected && !saved) return null;
    const label = (item: Delivery) => item.definitionName || item.definitionId || t("automation.reconciliation.task");
    const time = (value: string) => Number.isNaN(Date.parse(value)) ? "—" : new Date(value).toLocaleString(locale);

    return <section className="mx-auto w-full max-w-[1040px]" aria-label={t("automation.reconciliation.title")}>
        <ConfigCard title="automation.reconciliation.title" variant="list">
            <div className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground" role="status">{saved ? t("automation.reconciliation.saved") : items.length ? t("automation.reconciliation.unknown") : ""}</span>
                    <Button variant="ghost" size="sm" disabled={loading || saving} onClick={() => void load()}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />{t("automation.reconciliation.refresh")}</Button>
                </div>
                {loadError ? <p role="alert" className="text-sm text-destructive">{loadError}</p> : null}
                <ul className="max-h-[360px] divide-y divide-border overflow-y-auto">
                    {items.map(item => <li key={item.deliveryId} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                        <div className="min-w-0">
                            <p className="truncate text-sm font-medium" title={label(item)}>{label(item)}</p>
                            <p className="break-all text-xs text-muted-foreground">{time(item.createdAt)} · {item.deliveryId}</p>
                        </div>
                        <Button variant="outline" size="sm" className="shrink-0" disabled={loading || saving || Boolean(loadError)} onClick={() => open(item)}>{t("automation.reconciliation.review")}</Button>
                    </li>)}
                </ul>
                {nextCursor ? <Button variant="ghost" size="sm" disabled={loading || saving} onClick={() => void load(nextCursor)}>{t("automation.reconciliation.more")}</Button> : null}
            </div>
        </ConfigCard>
        <Dialog open={Boolean(selected)} onOpenChange={value => { if (!value && !submitting.current) setSelected(null); }}>
            <DialogContent guardUnsaved showCloseButton={!saving} onEscapeKeyDown={event => { if (saving) event.preventDefault(); }} onPointerDownOutside={event => { if (saving) event.preventDefault(); }}>
                <DialogHeader>
                    <DialogTitle>{t("automation.reconciliation.dialogTitle")}</DialogTitle>
                    <DialogDescription>{t("automation.reconciliation.description")}</DialogDescription>
                </DialogHeader>
                <form onSubmit={event => void submit(event)} className="space-y-4">
                    {selected ? <div className="min-w-0 rounded-lg bg-muted/50 p-3"><p className="break-words text-sm font-medium">{label(selected)}</p><p className="break-all text-xs text-muted-foreground">{time(selected.createdAt)} · {selected.deliveryId}</p></div> : null}
                    <fieldset disabled={saving} className="space-y-2">
                        <legend className="text-sm font-medium">{t("automation.reconciliation.outcome")}</legend>
                        <div className="flex gap-4">{(["completed", "failed"] as const).map(value => <label key={value} className="flex min-h-9 cursor-pointer items-center gap-2 text-sm"><input required type="radio" name={`${fieldId}-outcome`} value={value} checked={outcome === value} onChange={() => setOutcome(value)} className="h-4 w-4 accent-primary" />{t(`automation.reconciliation.${value}`)}</label>)}</div>
                    </fieldset>
                    <div className="space-y-2">
                        <Label htmlFor={`${fieldId}-observation`}>{t("automation.reconciliation.evidence")}</Label>
                        <Textarea id={`${fieldId}-observation`} required maxLength={2000} rows={4} disabled={saving} value={observation} onChange={event => setObservation(event.target.value)} placeholder={t("automation.reconciliation.evidencePlaceholder")} aria-describedby={`${fieldId}-hint`} />
                        <p id={`${fieldId}-hint`} className="text-xs leading-relaxed text-muted-foreground">{t("automation.reconciliation.evidenceHint")}</p>
                    </div>
                    <div className="space-y-2"><Label htmlFor={`${fieldId}-reference`}>{t("automation.reconciliation.reference")}</Label><Input id={`${fieldId}-reference`} maxLength={1000} disabled={saving} value={reference} onChange={event => setReference(event.target.value)} /></div>
                    {saveError ? <p role="alert" className="text-sm text-destructive">{saveError}</p> : null}
                    {refreshRequired ? <Button type="button" variant="outline" size="sm" disabled={loading || saving} onClick={() => void refreshReview()}>{t("automation.reconciliation.refresh")}</Button> : null}
                    {loadError ? <p role="alert" className="text-sm text-destructive">{loadError}</p> : null}
                    <DialogFooter className="sticky bottom-0 border-t border-border bg-background pt-3">
                        {saving ? <InlineSaveState saving saved={false} label="automation.reconciliation.record" /> : null}
                        <Button type="button" variant="outline" disabled={saving} onClick={() => setSelected(null)}>{t("automation.reconciliation.cancel")}</Button>
                        <Button type="submit" disabled={saving || loading || refreshRequired || !outcome || !observation.trim()}>{t("automation.reconciliation.save")}</Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    </section>;
}
