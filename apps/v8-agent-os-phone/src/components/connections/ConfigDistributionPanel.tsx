import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useIsFocused } from "@react-navigation/native";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { DistributionLocalWorkspaces } from "./DistributionLocalWorkspaces";
import {
    actOnDistribution, createDistribution, distributionCommandId, distributionMappingReady, distributionPlanKey,
    distributionValue, loadDistribution, loadDistributionJob, loadDistributionTarget, setDistributionRole, remapDistribution, loadDistributionHistory,
    type DistributionAction, type DistributionCapabilities, type DistributionCatalog, type DistributionFetch,
    type DistributionJob, type DistributionMapping, type DistributionPeer,
} from "@/src/lib/config-distribution";

export function ConfigDistributionPanel({ onClose, onChooseDevice }: { onClose: () => void; onChooseDevice?: () => void }) {
    const { authorityKey, servingInstanceId, authorizedFetch } = useAppSession();
    return <DistributionContent key={authorityKey} instanceId={servingInstanceId} fetcher={authorizedFetch} onClose={onClose} onChooseDevice={onChooseDevice} />;
}

function DistributionContent({ instanceId, fetcher, onClose, onChooseDevice }: { instanceId: string; fetcher: DistributionFetch; onClose: () => void; onChooseDevice?: () => void }) {
    const { colors, t } = useUiPrefs();
    const focused = useIsFocused();
    const visible = useAppVisibility();
    const [catalog, setCatalog] = useState<DistributionCatalog | null>(null);
    const [templateId, setTemplateId] = useState("");
    const [selected, setSelected] = useState<Record<string, DistributionMapping>>({});
    const [capabilities, setCapabilities] = useState<Record<string, DistributionCapabilities>>({});
    const [targetErrors, setTargetErrors] = useState<Record<string, string>>({});
    const [job, setJob] = useState<DistributionJob | null>(null);
    const [editingJob, setEditingJob] = useState<DistributionJob | null>(null);
    const [localSettings, setLocalSettings] = useState(false);
    const [expandedTarget, setExpandedTarget] = useState<string | null>(null);
    const [reviewed, setReviewed] = useState("");
    const [pendingDecision, setPendingDecision] = useState<{ action: "cancel" | "withdraw"; planKey: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const active = useRef(false);
    const requests = useRef(new Set<AbortController>());
    const mutation = useRef(false);
    const jobRef = useRef<DistributionJob | null>(null);
    // Reuse the same command after an ambiguous transport failure. No background write retries.
    const commands = useRef(new Map<string, string>());
    const text = (key: string, params?: Record<string, string | number>) => t(`phone.configDistribution.${key}`, params);
    const template = catalog?.templates.find((item) => item.id === templateId);
    const primaryPeers = catalog?.peers.filter((peer) => peer.localRole === "primary") || [];
    const checkedPeers = primaryPeers.filter((peer) => Boolean(selected[peer.linkId]));
    const canCreate = Boolean(template && checkedPeers.length && checkedPeers.length <= (catalog?.maxTargets || 100) && (template.id !== "model-roles" || template.roles?.length));
    const readyCount = template ? checkedPeers.filter((peer) => distributionMappingReady(template, selected[peer.linkId], capabilities[peer.linkId])).length : 0;
    const planKey = job ? distributionPlanKey(job) : "";
    const prepared = job?.targets.filter((target) => target.state === "prepared" && !target.approved) || [];
    const operationInProgress = Boolean(job && ["preparing", "applying", "cancelling", "withdrawing"].includes(job.state));
    const locked = busy || operationInProgress;
    const allowed = (action: DistributionAction) => job?.allowedActions ? job.allowedActions.includes(action) :
        action === "retry" ? Boolean(job?.targets.some((target) => ["offline", "recovery_required"].includes(target.state))) :
        action === "withdraw" ? Boolean(job?.targets.some((target) => ["committed", "recovery_required"].includes(target.state))) :
        !["cancelled", "withdrawn", "completed", "withdrawal_conflict"].includes(job?.state || "");

    const abortRequests = useCallback(() => {
        requests.current.forEach((controller) => controller.abort());
        requests.current.clear();
    }, []);
    const request = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> => {
        if (!active.current) return;
        const controller = new AbortController();
        requests.current.add(controller);
        try {
            const result = await operation(controller.signal);
            return active.current && !controller.signal.aborted ? result : undefined;
        } catch (failure) {
            if (active.current && !controller.signal.aborted) throw failure;
        } finally { requests.current.delete(controller); }
    }, []);
    const acceptJob = useCallback((next: DistributionJob) => {
        if (next.summary) return;
        const current = jobRef.current;
        if (current?.jobId === next.jobId && (current.revision > next.revision ||
            (current.revision === next.revision && current.updatedAt > next.updatedAt))) return;
        if (!current || distributionPlanKey(current) !== distributionPlanKey(next)) { setReviewed(""); setPendingDecision(null); }
        jobRef.current = next;
        if (current?.jobId !== next.jobId) setExpandedTarget(next.targets[0]?.linkId || null);
        setJob(next);
        const summary = { ...next, summary: true, targets: [], targetCount: next.targets.length, targetNames: next.targets.slice(0, 3).map((target) => target.displayName) };
        setCatalog((previous) => previous ? { ...previous, jobs: [summary, ...previous.jobs.filter((item) => item.jobId !== next.jobId)] } : previous);
    }, []);
    const refresh = useCallback(async () => {
        const result = await request((signal) => loadDistribution(fetcher, instanceId, signal));
        if (!result) return;
        setCatalog(result);
        setTemplateId((previous) => result.templates.some((item) => item.id === previous) ? previous : result.templates[0]?.id || "");
        const current = jobRef.current;
        if (current) {
            const updated = result.jobs.find((item) => item.jobId === current.jobId);
            if (updated && !updated.summary) acceptJob(updated);
        }
        setError("");
    }, [acceptJob, fetcher, instanceId, request]);
    useEffect(() => {
        active.current = focused && visible;
        if (!active.current) return;
        setBusy(mutation.current);
        void refresh().catch((failure) => setError(failure.message));
        let polling = false;
        const timer = setInterval(() => {
            const current = jobRef.current;
            if (!current || mutation.current || polling) return;
            polling = true;
            void request((signal) => loadDistributionJob(fetcher, current.jobId, signal)).then((next) => {
                if (next && jobRef.current?.jobId === current.jobId) { acceptJob(next); setError(""); }
            }).catch((failure) => setError(failure.message)).finally(() => { polling = false; });
        }, 2000);
        return () => { active.current = false; clearInterval(timer); abortRequests(); setReviewed(""); setPendingDecision(null); };
    }, [abortRequests, acceptJob, fetcher, focused, refresh, request, visible]);

    async function run(operation: () => Promise<void>) {
        if (mutation.current || !active.current) return;
        mutation.current = true;
        // Let bounded capability reads finish. Aborting shared endpoint
        // verification here can abort the immediately following write too.
        // Poll responses already carry job/revision guards; profile changes
        // and unmount still abort every request.
        setBusy(true); setError("");
        try { await operation(); }
        catch (failure) { if (active.current) setError(failure instanceof Error ? failure.message : "distribution_unavailable"); }
        finally { mutation.current = false; if (active.current) setBusy(false); }
    }
    function command(key: string) {
        if (!commands.current.has(key)) commands.current.set(key, distributionCommandId());
        return commands.current.get(key)!;
    }
    async function loadTarget(peer: DistributionPeer) {
        try {
            const result = await request((signal) => loadDistributionTarget(fetcher, peer, signal));
            if (result) { setCapabilities((current) => ({ ...current, [peer.linkId]: result })); setTargetErrors((current) => ({ ...current, [peer.linkId]: "" })); }
        } catch (failure) { setTargetErrors((current) => ({ ...current, [peer.linkId]: failure instanceof Error ? failure.message : "distribution_unavailable" })); }
    }
    function selectPeer(peer: DistributionPeer) {
        setSelected((current) => {
            if (current[peer.linkId]) { const next = { ...current }; delete next[peer.linkId]; return next; }
            return { ...current, [peer.linkId]: { roles: Object.fromEntries((template?.roles || []).map(({ id }) => [id, id])), models: {} } };
        });
        if (!selected[peer.linkId] && !capabilities[peer.linkId]) void loadTarget(peer);
    }
    function setMapping(linkId: string, kind: "roles" | "models", role: string, value: string) {
        setSelected((current) => ({ ...current, [linkId]: { ...current[linkId], [kind]: { ...current[linkId][kind], [role]: value } } }));
    }
    async function create() {
        if (!template || !canCreate) return;
        const input = { templateId: template.id, targets: checkedPeers.map((peer) => ({ linkId: peer.linkId, mapping: selected[peer.linkId] })) };
        await run(async () => {
            const next = await request((signal) => editingJob
                ? remapDistribution(fetcher, editingJob, input.targets, command(`${distributionPlanKey(editingJob)}:${JSON.stringify(input)}`), signal)
                : createDistribution(fetcher, { ...input, commandId: command(JSON.stringify(input)) }, signal));
            if (next) { acceptJob(next); setReviewed(""); setEditingJob(null); }
        });
    }
    async function act(action: DistributionAction) {
        const current = jobRef.current;
        if (!current || (action === "confirm" && (reviewed !== distributionPlanKey(current) || !current.targets.some((target) => target.state === "prepared")))) return;
        if ((action === "cancel" || action === "withdraw") && (pendingDecision?.action !== action || pendingDecision.planKey !== distributionPlanKey(current))) return;
        setPendingDecision(null);
        if (action === "prepare") setReviewed("");
        await run(async () => {
            const next = await request((signal) => actOnDistribution(fetcher, current, action, command(`${distributionPlanKey(current)}:${action}`), signal));
            if (next) { acceptJob(next); if (action === "confirm") setReviewed(""); }
        });
    }
    function newPlan() {
        abortRequests(); commands.current.clear(); jobRef.current = null; setJob(null); setEditingJob(null); setReviewed(""); setPendingDecision(null); setError("");
    }
    function editMappings() {
        if (!job) return;
        const remaining = job.targets.filter((target) => !target.approved && !["committed", "rolled_back", "cancelled"].includes(target.state));
        setEditingJob(job); setTemplateId(job.templateId); setCapabilities({});
        setSelected(Object.fromEntries(remaining.map((target) => [target.linkId, target.mapping || { roles: {}, models: {} }])));
        jobRef.current = null; setJob(null); setReviewed("");
        for (const target of remaining) { const peer = catalog?.peers.find((item) => item.linkId === target.linkId); if (peer) void loadTarget(peer); }
    }
    const button = (label: string, onPress: () => void, disabled = false, danger = false) => <Pressable accessibilityRole="button" disabled={disabled}
        accessibilityState={{ disabled }} onPress={onPress} style={[styles.button, { borderColor: colors.border, opacity: disabled ? 0.45 : 1 }]}>
        <Text style={{ color: danger ? colors.danger : colors.primary, fontWeight: "600" }}>{label}</Text>
    </Pressable>;
    const stateLabel = (state: string) => { const key = `phone.configDistribution.states.${state}`; const result = t(key); return result === key ? state : result; };
    const showError = (code: string) => { const key = `phone.configDistribution.errors.${code}`; const result = t(key); return result === key ? `${text("failed")} (${code})` : result; };
    const fieldLabel = (field: string) => {
        const key = `phone.configDistribution.fields.${field}`;
        const result = t(key);
        if (result !== key) return result;
        const role = field.match(/^(?:roleParameters|roles)\.([^.]+)/)?.[1];
        if (role) return text(field.startsWith("roles.") ? "modelField" : "temperatureField", { role });
        return field;
    };
    const templateLabel = (id: string, fallback: string) => { const key = `phone.configDistribution.templates.${id}.label`; const result = t(key); return result === key ? fallback : result; };
    const templateDescription = (id: string, fallback: string) => { const key = `phone.configDistribution.templates.${id}.description`; const result = t(key); return result === key ? fallback : result; };

    return <Modal visible animationType="slide" onRequestClose={onClose}>
        <SafeAreaView style={[styles.root, { backgroundColor: colors.background }]}>
            <View style={styles.heading}>
                <Text accessibilityRole="header" style={[styles.title, { color: colors.text }]}>{text("title")}</Text>
                {button(t("phone.devices.close"), onClose)}
            </View>
            <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
                <Text style={{ color: colors.textMuted, lineHeight: 21 }}>{text("scope")}</Text>
                <Text style={{ color: colors.textMuted }}>{text("bilateralGuide")}</Text>
                <View style={styles.choices}>
                    {onChooseDevice ? button(text("chooseDevice"), onChooseDevice, busy) : null}
                    {button(text("localWorkspace"), () => setLocalSettings(!localSettings), busy)}
                </View>
                {localSettings ? <DistributionLocalWorkspaces fetcher={fetcher} active={focused && visible} /> : null}
                {error ? <View accessibilityRole="alert" style={[styles.card, { backgroundColor: colors.surface }]}>
                    <Text style={{ color: colors.danger }}>{showError(error)}</Text>
                    <Text style={{ color: colors.textMuted }}>{text("unknownResult")}</Text>
                    {button(text("refresh"), () => void run(refresh), busy)}
                </View> : null}
                {busy ? <ActivityIndicator accessibilityLabel={text("loading")} color={colors.primary} /> : null}
                {!catalog && !error ? <ActivityIndicator color={colors.primary} /> : null}
                {catalog && !job ? <>
                    <Text accessibilityRole="header" style={[styles.section, { color: colors.text }]}>{text("template")}</Text>
                    {catalog.templates.map((item) => <Pressable key={item.id} accessibilityRole="radio" accessibilityLabel={templateLabel(item.id, item.label)} aria-checked={templateId === item.id}
                        accessibilityState={{ checked: templateId === item.id }} disabled={busy || Boolean(editingJob)} onPress={() => { setTemplateId(item.id); setSelected({}); }}
                        style={[styles.card, { backgroundColor: colors.surface, borderColor: templateId === item.id ? colors.primary : colors.border }]}>
                        <Text style={{ color: colors.text, fontWeight: "700" }}>{templateLabel(item.id, item.label)}</Text>
                        <Text style={{ color: colors.textMuted, lineHeight: 21 }}>{templateDescription(item.id, item.description)}</Text>
                    </Pressable>)}
                    <View style={styles.heading}><Text accessibilityRole="header" style={[styles.section, { color: colors.text }]}>{text("targets")}</Text>
                        {button(text("selectAll"), () => {
                            setSelected(Object.fromEntries(primaryPeers.map((peer) => [peer.linkId, selected[peer.linkId] || { roles: Object.fromEntries((template?.roles || []).map(({ id }) => [id, id])), models: {} }])));
                            for (const peer of primaryPeers) if (!capabilities[peer.linkId]) void loadTarget(peer);
                        }, busy || !primaryPeers.length || Boolean(editingJob))}
                    </View>
                    <Text style={{ color: colors.textMuted }}>{text("primaryHint")}</Text>
                    {checkedPeers.length > (catalog.maxTargets || 100) ? <Text style={{ color: colors.warning }}>{text("batchLimit", { count: catalog.maxTargets || 100 })}</Text> : null}
                    {template?.id === "model-roles" && !template.roles?.length ? <Text style={{ color: colors.warning }}>{text("noSourceRoles")}</Text> : null}
                    {!catalog.peers.length ? <Text style={{ color: colors.textMuted }}>{text("noTargets")}</Text> : null}
                    {catalog.peers.map((peer) => {
                        const mapping = selected[peer.linkId];
                        const options = capabilities[peer.linkId];
                        return <View key={peer.linkId} style={[styles.card, { backgroundColor: colors.surface, borderColor: mapping ? colors.primary : colors.border }]}>
                            <Pressable accessibilityRole="checkbox" accessibilityLabel={peer.displayName} aria-checked={Boolean(mapping)} accessibilityState={{ checked: Boolean(mapping), disabled: peer.localRole !== "primary" || busy }}
                                disabled={peer.localRole !== "primary" || busy} onPress={() => selectPeer(peer)} style={styles.targetHeading}>
                                <Text style={{ color: colors.primary, fontSize: 20 }}>{mapping ? "☑" : "☐"}</Text>
                                <View style={{ flex: 1, gap: 4 }}><Text style={{ color: colors.text, fontWeight: "700" }}>{peer.displayName}</Text>
                                    <Text style={{ color: colors.textMuted }}>{text(peer.online ? "online" : "offline")} · {text(peer.localRole === "primary" ? "primary" : "companion")}</Text></View>
                            </Pressable>
                            {button(text(peer.localRole === "primary" ? "makeCompanion" : "makePrimary"), () => void run(async () => {
                                const result = await request((signal) => setDistributionRole(fetcher, peer, peer.localRole === "primary" ? "companion" : "primary", signal));
                                if (result !== undefined) { setSelected({}); setCapabilities({}); await refresh(); }
                            }), busy)}
                            {mapping ? <>
                                <Text style={{ color: colors.textMuted, fontSize: 12 }}>{text("localPaths")}</Text>
                                {targetErrors[peer.linkId] ? <View><Text style={{ color: colors.warning }}>{showError(targetErrors[peer.linkId])}</Text>
                                    </View> : null}
                                {button(text("reloadTarget"), () => void loadTarget(peer), busy)}
                                {(template?.roles || []).map((role) => <View key={role.id} style={styles.mapping}>
                                    <Text style={{ color: colors.text, fontWeight: "600" }}>{role.label} → {text("targetRole")}</Text>
                                    {options ? <View style={styles.choices}>{options.roles.map((targetRole) => <Pressable key={targetRole.id} accessibilityRole="radio"
                                        accessibilityLabel={`${peer.displayName}: ${role.label} → ${targetRole.label}`} aria-checked={mapping.roles[role.id] === targetRole.id} accessibilityState={{ checked: mapping.roles[role.id] === targetRole.id }} disabled={busy}
                                        onPress={() => setMapping(peer.linkId, "roles", role.id, targetRole.id)} style={[styles.choice, { borderColor: mapping.roles[role.id] === targetRole.id ? colors.primary : colors.border }]}>
                                        <Text style={{ color: colors.text }}>{targetRole.label}</Text></Pressable>)}</View> : <Text style={{ color: colors.textMuted }}>{mapping.roles[role.id]}</Text>}
                                    {template?.id === "model-roles" ? <>
                                        <Text style={{ color: colors.textMuted }}>{text("chooseModel")}</Text>
                                        {!options ? <Text style={{ color: colors.warning }}>{text("needsOnlineMapping")}</Text> : null}
                                        {options?.models.map((model) => <Pressable key={model.modelRef} accessibilityRole="radio"
                                            accessibilityLabel={`${peer.displayName}: ${role.label} → ${model.label}`} aria-checked={mapping.models[role.id] === model.modelRef} accessibilityState={{ checked: mapping.models[role.id] === model.modelRef, disabled: busy || !model.ready || Boolean(model.missingRequirements.length) }}
                                            disabled={busy || !model.ready || Boolean(model.missingRequirements.length)} onPress={() => setMapping(peer.linkId, "models", role.id, model.modelRef)}
                                            style={[styles.choice, { borderColor: mapping.models[role.id] === model.modelRef ? colors.primary : colors.border }]}>
                                            <Text style={{ color: model.ready ? colors.text : colors.textMuted }}>{model.label}</Text>
                                            {!model.ready || model.missingRequirements.length ? <Text style={{ color: colors.warning }}>{text("missingLocal")}: {model.missingRequirements.map(showError).join(" ") || text("modelNotReady")}</Text> : null}
                                        </Pressable>)}
                                        {options && !options.models.length ? <Text style={{ color: colors.warning }}>{text("noModels")}</Text> : null}
                                    </> : null}
                                </View>)}
                            </> : null}
                        </View>;
                    })}
                    {button(text("preview", { count: checkedPeers.length }), () => void create(), busy || !canCreate)}
                    {readyCount < checkedPeers.length ? <Text style={{ color: colors.warning }}>{text("partialMapping", { ready: readyCount, pending: checkedPeers.length - readyCount })}</Text> : null}
                    {editingJob ? button(text("keep"), () => { acceptJob(editingJob); setEditingJob(null); }, busy) : null}
                    <Text style={{ color: colors.textMuted }}>{text("previewHint")}</Text>
                    {catalog.jobs.length ? <Text accessibilityRole="header" style={[styles.section, { color: colors.text }]}>{text("recent")}</Text> : null}
                    {catalog.jobs.map((item) => <Pressable key={item.jobId} accessibilityRole="button" disabled={busy} onPress={() => void run(async () => { const next = await request((signal) => loadDistributionJob(fetcher, item.jobId, signal)); if (next) { setReviewed(""); acceptJob(next); } })}
                        style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <Text style={{ color: colors.text, fontWeight: "600" }}>{templateLabel(item.templateId, catalog.templates.find((entry) => entry.id === item.templateId)?.label || item.templateId)} · {stateLabel(item.state)}</Text>
                        <Text style={{ color: colors.textMuted }}>{(item.targetNames || item.targets.map((target) => target.displayName)).join("、")} · {item.targetCount ?? item.targets.length} · {new Date(item.updatedAt).toLocaleString()}</Text>
                    </Pressable>)}
                    {catalog.jobsNextCursor ? button(text("moreJobs"), () => void run(async () => {
                        const next = await request((signal) => loadDistributionHistory(fetcher, catalog.jobsNextCursor!, signal));
                        if (next) setCatalog((previous) => previous ? { ...previous, jobs: [...previous.jobs, ...next.items.filter((item) => !previous.jobs.some((old) => old.jobId === item.jobId))], jobsNextCursor: next.nextCursor } : previous);
                    }), busy) : null}
                </> : null}
                {job ? <>
                    <View style={styles.heading}><Text accessibilityRole="header" style={[styles.section, { color: colors.text }]}>{stateLabel(job.state)}</Text>{button(text("newPlan"), newPlan, busy)}</View>
                    <Text style={{ color: colors.textMuted }}>{text("progress", { done: job.targets.filter((target) => target.state === "committed").length, total: job.targets.length })}</Text>
                    {job.targets.map((target) => <View key={target.linkId} style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <Text accessibilityRole="header" style={{ color: colors.text, fontWeight: "700", fontSize: 16 }}>{target.displayName}</Text>
                        <Text style={{ color: target.state === "committed" || target.state === "rolled_back" ? colors.success : colors.textMuted }}>{stateLabel(target.state)}</Text>
                        {button(text(expandedTarget === target.linkId ? "collapseTarget" : "expandTarget", { name: target.displayName }), () => setExpandedTarget(expandedTarget === target.linkId ? null : target.linkId))}
                        {expandedTarget === target.linkId ? <>
                        <Text style={{ color: colors.textMuted }}>{text(target.approved ? "approvedIntent" : "unapprovedIntent")}</Text>
                        {Object.entries(target.mapping?.roles || {}).map(([source, targetRole]) => <Text key={source} style={{ color: colors.textMuted }}>{source} → {targetRole}{target.mapping?.models[source] ? ` · ${target.mapping.models[source]}` : ""}</Text>)}
                        {target.errorCode ? <Text style={{ color: colors.warning }}>{showError(target.errorCode)}</Text> : null}
                        {target.missingRequirements.length ? <Text style={{ color: colors.warning }}>{text("missingLocal")}: {target.missingRequirements.map(showError).join(" ")}</Text> : null}
                        {target.diff.map((diff, index) => <View key={`${diff.field}:${index}`} style={[styles.diff, { borderColor: colors.border }]}>
                            <Text selectable style={{ color: colors.text, fontWeight: "600" }}>{fieldLabel(diff.field)}</Text>
                            <Text selectable style={{ color: colors.textMuted }}>{text("before")}: {distributionValue(diff.before)}</Text>
                            <Text selectable style={{ color: colors.text }}>{text("after")}: {distributionValue(diff.after)}</Text>
                        </View>)}
                        {target.state === "prepared" && !target.diff.length ? <Text style={{ color: colors.textMuted }}>{text("noChanges")}</Text> : null}
                        {target.receipt?.transactionId ? <View style={styles.mapping}>
                            <Text style={{ color: colors.textMuted, fontSize: 12 }}>{text("receipt")}: {target.receipt.transactionId} · {stateLabel(target.receipt.state)}</Text>
                            {target.receipt.readback && typeof target.receipt.readback === "object" ? <>
                                <Text style={{ color: colors.textMuted, fontSize: 12 }}>{text("readback")}</Text>
                                {Object.entries(target.receipt.readback).map(([field, value]) => <Text key={field} selectable style={{ color: colors.textMuted, fontSize: 12 }}>{fieldLabel(field)}: {distributionValue(value)}</Text>)}
                            </> : null}
                        </View> : null}
                        </> : null}
                    </View>)}
                    {prepared.length && allowed("confirm") ? <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.primary }]}>
                        <Text style={{ color: colors.text }}>{text("confirmScope", { ready: prepared.length, other: job.targets.length - prepared.length })}</Text>
                        <Pressable accessibilityRole="checkbox" accessibilityLabel={text("reviewed")} aria-checked={reviewed === planKey} accessibilityState={{ checked: reviewed === planKey, disabled: locked }} disabled={locked}
                            onPress={() => setReviewed(reviewed === planKey ? "" : planKey)} style={styles.targetHeading}>
                            <Text style={{ color: colors.primary, fontSize: 20 }}>{reviewed === planKey ? "☑" : "☐"}</Text><Text style={{ color: colors.text, flex: 1 }}>{text("reviewed")}</Text>
                        </Pressable>
                        {button(text("confirm", { count: prepared.length }), () => void act("confirm"), locked || reviewed !== planKey)}
                    </View> : null}
                    <Text style={{ color: colors.textMuted }}>{text(job.state === "withdrawal_conflict" ? "withdrawConflict" : job.intent === "cancel" || job.intent === "withdraw" ? "cleanupRecovery" : "recoveryHint")}</Text>
                    <View style={styles.choices}>
                        {button(text("prepare"), () => void act("prepare"), locked || !allowed("prepare"))}
                        {button(text("retry"), () => void act("retry"), locked || !allowed("retry"))}
                        {button(text("cancel"), () => setPendingDecision({ action: "cancel", planKey }), busy || !allowed("cancel"), true)}
                        {button(text("withdraw"), () => setPendingDecision({ action: "withdraw", planKey }), busy || !allowed("withdraw"), true)}
                        {allowed("prepare") && job.targets.some((target) => !target.approved && !["committed", "cancelled", "rolled_back"].includes(target.state)) ? button(text("editMappings"), editMappings, locked) : null}
                    </View>
                    {pendingDecision ? <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.warning }]}>
                        <Text style={{ color: colors.text }}>{text(pendingDecision.action === "cancel" ? "cancelHint" : "withdrawHint")}</Text>
                        {button(text(pendingDecision.action === "cancel" ? "confirmCancel" : "confirmWithdraw"), () => void act(pendingDecision.action), busy, true)}
                        {button(text("keep"), () => setPendingDecision(null))}
                    </View> : null}
                </> : null}
            </ScrollView>
        </SafeAreaView>
    </Modal>;
}

const styles = StyleSheet.create({
    root: { flex: 1 }, heading: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, paddingHorizontal: 16 },
    title: { flex: 1, fontSize: 21, fontWeight: "700" }, section: { flex: 1, fontSize: 17, fontWeight: "700" },
    content: { padding: 16, paddingBottom: 40, gap: 12 }, card: { padding: 14, borderRadius: 14, borderWidth: 1, gap: 10 },
    button: { minHeight: 44, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, alignItems: "center", justifyContent: "center" },
    targetHeading: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: 10 }, mapping: { gap: 8, paddingTop: 8 },
    choices: { flexDirection: "row", flexWrap: "wrap", gap: 8 }, choice: { minHeight: 44, borderWidth: 1, borderRadius: 10, padding: 10, gap: 5 },
    diff: { borderTopWidth: 1, paddingTop: 10, gap: 6 },
});
