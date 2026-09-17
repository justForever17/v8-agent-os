import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { bindLocalWorkspace, loadLocalWorkspaces, type DistributionFetch, type LocalWorkspaceCatalog } from "@/src/lib/config-distribution";

export function DistributionLocalWorkspaces({ fetcher, active }: { fetcher: DistributionFetch; active: boolean }) {
    const { colors, t } = useUiPrefs();
    const text = (key: string) => t(`phone.configDistribution.${key}`);
    const [catalog, setCatalog] = useState<LocalWorkspaceCatalog | null>(null);
    const [selection, setSelection] = useState<{linkId: string; projectId: string} | null>(null);
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [reload, setReload] = useState(0);
    useEffect(() => {
        if (!active) return;
        const controller = new AbortController();
        setSelection(null); setConfirmed(false);
        void loadLocalWorkspaces(fetcher, controller.signal).then((next) => { if (!controller.signal.aborted) { setCatalog(next); setError(""); } })
            .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
        return () => controller.abort();
    }, [active, fetcher, reload]);
    async function save() {
        if (!active || busy || !confirmed || !selection || !catalog) return;
        const link = catalog.links.find((item) => item.linkId === selection.linkId);
        const project = catalog.projects.find((item) => item.projectId === selection.projectId);
        if (!link || !project) return;
        setBusy(true);
        try { const next = await bindLocalWorkspace(fetcher, link, project); setCatalog(next); setSelection(null); setConfirmed(false); setError(""); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "distribution_unavailable"); }
        finally { setBusy(false); }
    }
    return <View style={{ gap: 12, padding: 12, backgroundColor: colors.surface, borderRadius: 12 }}>
        <Text style={{ color: colors.text, fontWeight: "700" }}>{text("localWorkspace")}</Text>
        <Text style={{ color: colors.textMuted }}>{text("workspaceGuide")}</Text>
        {error ? <Text accessibilityRole="alert" style={{ color: colors.warning }}>{text("workspaceError")} ({error})</Text> : null}
        <Pressable accessibilityRole="button" onPress={() => setReload((value) => value + 1)} disabled={busy}><Text style={{ color: colors.primary, paddingVertical: 12 }}>{text("refresh")}</Text></Pressable>
        {!catalog || busy ? <ActivityIndicator /> : null}
        {catalog && !catalog.projects.length ? <Text style={{ color: colors.textMuted }}>{text("noLocalProjects")}</Text> : null}
        {catalog?.links.map((link) => <View key={link.linkId} style={{ gap: 8 }}>
            <Text style={{ color: colors.text }}>{link.displayName} · {link.localPath || text("defaultWorkspace")}</Text>
            {catalog.projects.map((project) => <Pressable key={project.projectId} accessibilityRole="radio" accessibilityLabel={`${link.displayName} → ${project.label}`}
                accessibilityState={{ checked: selection?.linkId === link.linkId && selection?.projectId === project.projectId }} disabled={busy}
                onPress={() => { setSelection({linkId: link.linkId, projectId: project.projectId}); setConfirmed(false); }} style={{ padding: 12, borderWidth: 1, borderColor: selection?.linkId === link.linkId && selection?.projectId === project.projectId ? colors.primary : colors.border, borderRadius: 8 }}>
                <Text style={{ color: colors.text }}>{project.label}</Text><Text selectable style={{ color: colors.textMuted }}>{project.localPath}</Text>
            </Pressable>)}
        </View>)}
        {selection ? <>
            <Pressable accessibilityRole="checkbox" accessibilityLabel={text("trustLocal")} accessibilityState={{ checked: confirmed }} disabled={busy} onPress={() => setConfirmed(!confirmed)}>
                <Text style={{ color: colors.text, paddingVertical: 12 }}>{confirmed ? "☑" : "☐"} {text("trustLocal")}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" disabled={!confirmed || busy} accessibilityState={{ disabled: !confirmed || busy }} onPress={() => void save()}>
                <Text style={{ color: confirmed ? colors.primary : colors.textMuted, paddingVertical: 12 }}>{text("bindLocal")}</Text>
            </Pressable>
        </> : null}
    </View>;
}
