"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2, RefreshCw } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ConfigCard } from "./ConfigCard";

type Action = "unlock" | "run_privileged";
type Profile = { configured: boolean; username: string; domain: string };
type Component = "unlock" | "privilege";
type Setup = { state: string; component?: Component; action?: string };
type Account = { username: string; domain: string };
type Settings = { profiles: Record<Action, Profile>; currentAccount?: Account; platform: { os: string; unlock?: { registered?: boolean }; privilege?: { registered?: boolean }; setup?: Setup } };

function CredentialForm({ action, profile, windows, currentAccount, onSaved }: { action: Action; profile: Profile; windows: boolean; currentAccount?: Account; onSaved: () => Promise<void> }) {
    const t = useT();
    const [username, setUsername] = useState(profile.username);
    const [domain, setDomain] = useState(profile.domain);
    const [password, setPassword] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    useEffect(() => { setUsername(profile.username); setDomain(profile.domain); }, [profile.username, profile.domain]);
    async function submit(remove = false) {
        setBusy(true); setError("");
        try {
            const response = await fetch(`/api/system-operations/credentials/${action}`, {
                method: remove ? "DELETE" : "PUT",
                headers: { "Content-Type": "application/json" },
                body: remove ? undefined : JSON.stringify({ username, domain, password }),
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                const code = body?.detail?.code;
                setError(t(["system_account_invalid", "system_account_not_found", "unlock_current_account_required"].includes(code) ? `systemOperations.${code}` : "systemOperations.saveFailed"));
                return;
            }
            setPassword("");
            await onSaved();
        } catch { setError(t("systemOperations.saveFailed")); }
        finally { setPassword(""); setBusy(false); }
    }
    return <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="min-w-0 space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{t(`systemOperations.${action}`)}</h3><span className="text-xs text-muted-foreground">{t(profile.configured ? "systemOperations.configured" : "systemOperations.notConfigured")}</span></div>
        <p className="text-xs text-muted-foreground">{t(`systemOperations.${action}Hint`)}</p>
        {currentAccount && <Button type="button" variant="link" className="h-auto p-0" disabled={busy} onClick={() => { setUsername(currentAccount.username); setDomain(currentAccount.domain); }}>{t("systemOperations.useCurrentAccount")}</Button>}
        <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1"><Label htmlFor={`${action}-account`}>{t("systemOperations.account")}</Label><Input id={`${action}-account`} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" maxLength={256} required disabled={busy} /></div>
            <div className="space-y-1"><Label htmlFor={`${action}-domain`}>{t("systemOperations.domain")}</Label><Input id={`${action}-domain`} value={domain} onChange={(e) => setDomain(e.target.value)} autoComplete="off" maxLength={256} disabled={busy} /></div>
        </div>
        <div className="space-y-1"><Label htmlFor={`${action}-password`}>{t("systemOperations.password")}</Label><Input id={`${action}-password`} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" maxLength={windows ? 1024 : 4096} required disabled={busy} placeholder={t("systemOperations.passwordHint")} /></div>
        <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || !username.trim() || !password}>{busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <KeyRound className="mr-2 h-4 w-4" />}{t("systemOperations.save")}</Button><Button type="button" variant="outline" disabled={busy || !profile.configured} onClick={() => void submit(true)}>{t("systemOperations.remove")}</Button></div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </form>;
}

export function SystemOperationsCard() {
    const t = useT();
    const [settings, setSettings] = useState<Settings | null>(null);
    const [failed, setFailed] = useState(false);
    const [loading, setLoading] = useState(false);
    const [settingUp, setSettingUp] = useState(false);
    const [setupError, setSetupError] = useState(false);
    const reload = useCallback(async () => {
        setLoading(true); setFailed(false);
        try {
            const response = await fetch("/api/system-operations/settings", { cache: "no-store" });
            if (!response.ok) throw new Error("status failed");
            setSettings(await response.json() as Settings);
        } catch { setFailed(true); }
        finally { setLoading(false); }
    }, []);
    useEffect(() => { void reload(); }, [reload]);
    const setupState = settings?.platform.setup?.state || "idle";
    const setupPending = setupState === "awaiting_os_approval" || setupState === "running";
    useEffect(() => {
        if (!setupPending) return;
        // Keep one request in flight, and stop polling when this card unmounts.
        let disposed = false;
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => { await reload(); if (!disposed) timer = setTimeout(poll, 2000); };
        timer = setTimeout(poll, 2000);
        return () => { disposed = true; clearTimeout(timer); };
    }, [reload, setupPending]);
    async function setup(component: Component, action: "install" | "uninstall") {
        setSettingUp(true); setSetupError(false);
        try {
            const response = await fetch(`/api/system-operations/components/${component}/${action}`, { method: "POST" });
            if (!response.ok) throw new Error("setup unavailable");
            await reload();
        } catch { setSetupError(true); }
        finally { setSettingUp(false); }
    }
    return <ConfigCard title={t("systemOperations.title")} description={t("systemOperations.description")} bodyScroll="none" allowOverflow>
        <div className="flex items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{t("systemOperations.approvalHint")}</p><Button type="button" variant="ghost" size="icon" disabled={loading} aria-label={t("systemOperations.refresh")} onClick={() => void reload()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /></Button></div>
        {failed && <p role="alert" className="text-sm text-destructive">{t("systemOperations.loadFailed")}</p>}
        {settings && <>
            <p className="text-sm text-muted-foreground">{t(settings.platform.os === "windows" ? settings.platform.unlock?.registered ? "systemOperations.unlockInstalled" : "systemOperations.unlockInstallRequired" : "systemOperations.platformUnlockUnavailable")}</p>
            {settings.platform.os === "windows" && <div className="space-y-2 rounded-lg border p-3">
                <p className="text-sm text-muted-foreground">{t("systemOperations.setupHint")}</p>
                {(["privilege", "unlock"] as const).map((component) => <div key={component} className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm">{t(`systemOperations.component.${component}`)} · {t(settings.platform[component]?.registered ? "systemOperations.installed" : "systemOperations.notInstalled")}</span>
                    <div className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={settingUp || setupPending} onClick={() => void setup(component, "install")}>{t(settings.platform[component]?.registered ? "systemOperations.repairComponent" : "systemOperations.installComponent")}</Button>
                        {settings.platform[component]?.registered && <Button type="button" size="sm" variant="ghost" disabled={settingUp || setupPending} onClick={() => void setup(component, "uninstall")}>{t("systemOperations.uninstallComponent")}</Button>}</div>
                </div>)}
                {setupState !== "idle" && <p role="status" className="text-sm">{t(`systemOperations.setup.${["awaiting_os_approval", "running", "completed", "failed", "cancelled"].includes(setupState) ? setupState : "failed"}`)}</p>}
                {setupError && <p role="alert" className="text-sm text-destructive">{t("systemOperations.setup.failed")}</p>}
            </div>}
            <div className="grid gap-4 lg:grid-cols-2">{(["run_privileged", "unlock"] as const).map((action) => <CredentialForm key={action} action={action} profile={settings.profiles[action]} windows={settings.platform.os === "windows"} currentAccount={settings.currentAccount} onSaved={reload} />)}</div>
        </>}
    </ConfigCard>;
}
