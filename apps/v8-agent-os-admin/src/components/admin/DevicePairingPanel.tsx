"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Clipboard, Loader2, QrCode, RefreshCw, Settings2, Trash2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";

type PairingTicket = {
    pairingId: string;
    instanceId: string;
    surface: "phone";
    adminBaseUrl: string;
    pairingCode: string;
    pairingUri: string;
    expiresAt: string;
};

type DeviceSession = {
    id: string;
    deviceName: string;
    createdAt: string;
    expiresAt: string;
};

type DeviceSessionsPayload = { devices?: DeviceSession[] };
const DEVICE_SESSIONS_URL = "/api/client/devices";
type PairingManifest = { instanceId?: string; pairing?: { available: boolean; baseUrl: string; reason: string } };

export function DevicePairingPanel({ onConfigure }: { onConfigure?: () => void } = {}) {
    const t = useT();
    const cachedDevices = peekAdminJsonCache<DeviceSessionsPayload>(DEVICE_SESSIONS_URL);
    const [ticket, setTicket] = useState<PairingTicket | null>(null);
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState("");
    const [devices, setDevices] = useState<DeviceSession[]>(() => cachedDevices?.devices || []);
    const [devicesBusy, setDevicesBusy] = useState(() => !cachedDevices);
    const [revokingId, setRevokingId] = useState("");
    const [qrDataUrl, setQrDataUrl] = useState("");
    const [manifest, setManifest] = useState<PairingManifest | null>(null);
    const [addressBusy, setAddressBusy] = useState(true);
    const [addressFailed, setAddressFailed] = useState(false);
    const loadAddress = useCallback(async (signal?: AbortSignal) => {
        setAddressBusy(true);
        setAddressFailed(false);
        try {
            const response = await fetch("/api/client/link/manifest", { cache: "no-store", signal });
            if (!response.ok) throw new Error("manifest_unavailable");
            const next = await response.json() as PairingManifest;
            if (!next.pairing) throw new Error("pairing_manifest_unavailable");
            if (!signal?.aborted) setManifest(next);
        } catch {
            if (!signal?.aborted) { setManifest(null); setAddressFailed(true); }
        } finally {
            if (!signal?.aborted) setAddressBusy(false);
        }
    }, []);

    const loadDevices = useCallback(async (force = false) => {
        if (!peekAdminJsonCache<DeviceSessionsPayload>(DEVICE_SESSIONS_URL)) setDevicesBusy(true);
        try {
            const payload = await fetchAdminJson<DeviceSessionsPayload>(DEVICE_SESSIONS_URL, { force });
            setDevices(Array.isArray(payload?.devices) ? payload.devices : []);
        } catch {
            // Preserve the last known device list while the control plane recovers.
        } finally {
            setDevicesBusy(false);
        }
    }, []);

    useEffect(() => {
        void loadDevices();
    }, [loadDevices]);

    useEffect(() => {
        const controller = new AbortController();
        void loadAddress(controller.signal);
        return () => controller.abort();
    }, [loadAddress]);

    useEffect(() => {
        if (!ticket?.pairingUri) {
            setQrDataUrl("");
            return;
        }
        let cancelled = false;
        import("qrcode").then(({ default: QRCode }) => QRCode.toDataURL(ticket.pairingUri, {
            width: 240,
            margin: 1,
            errorCorrectionLevel: "M",
            color: { dark: "#0f172a", light: "#ffffff" },
        })).then((value) => {
            if (!cancelled) setQrDataUrl(value);
        }).catch(() => {
            if (!cancelled) { setQrDataUrl(""); setError(t("components.admin.DevicePairingPanel.createFailed")); }
        });
        return () => {
            cancelled = true;
        };
    }, [ticket, t]);

    async function createTicket() {
        if (addressBusy || !manifest?.pairing?.available) return;
        setBusy(true);
        setError("");
        setCopied(false);
        try {
            const response = await fetch("/api/client/pairing/tickets", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    surface: "phone",
                    deviceName: "v8-phone",
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload?.pairingCode || !payload?.pairingUri) {
                const reason = String(payload?.error || "");
                if (["pairing_reachable_https_required", "phone_gateway_disabled", "remote_link_disabled"].includes(reason)) {
                    void loadAddress();
                    throw new Error(t("components.admin.DevicePairingPanel.addressRequired"));
                }
                throw new Error(t("components.admin.DevicePairingPanel.createFailed"));
            }
            setTicket(payload as PairingTicket);
        } catch (nextError) {
            setTicket(null);
            setError(nextError instanceof Error ? nextError.message : t("components.admin.DevicePairingPanel.createFailed"));
        } finally {
            setBusy(false);
        }
    }

    async function copyPairingUri() {
        if (!ticket?.pairingUri) return;
        try {
            await navigator.clipboard.writeText(ticket.pairingUri);
            setCopied(true);
        } catch { setError(t("components.admin.DevicePairingPanel.copyFailed")); }
    }

    async function revokeDevice(deviceSessionId: string) {
        setRevokingId(deviceSessionId);
        setError("");
        try {
            const response = await fetch("/api/client/devices", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ deviceSessionId }),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(String(payload?.error || t("components.admin.DevicePairingPanel.revokeFailed")));
            }
            await loadDevices(true);
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t("components.admin.DevicePairingPanel.revokeFailed"));
        } finally {
            setRevokingId("");
        }
    }

    return (
        <div className="rounded-lg border border-border bg-muted/70 p-4 dark:border-slate-800 dark:bg-slate-950/40">
            <div className="mb-4 grid gap-2">
                <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-muted-foreground">{t("components.admin.DevicePairingPanel.phoneAddress")}</span>
                    <Button type="button" variant="ghost" size="icon" className="h-9 w-9" disabled={addressBusy || busy} onClick={() => { setTicket(null); void loadAddress(); }} aria-label={t("components.admin.DevicePairingPanel.refreshAddress")}>
                        {addressBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    </Button>
                </div>
                {ticket?.adminBaseUrl || manifest?.pairing?.baseUrl ? (
                    <>
                        <div className="break-all font-mono text-sm" data-testid="phone-pairing-address">{ticket?.adminBaseUrl || manifest?.pairing?.baseUrl}</div>
                        <p className="text-xs text-muted-foreground">{t("components.admin.DevicePairingPanel.addressHint")}</p>
                    </>
                ) : !addressBusy ? (
                    <p role="status" className="text-sm text-muted-foreground">{t(addressFailed ? "components.admin.DevicePairingPanel.addressLoadFailed" : "components.admin.DevicePairingPanel.addressRequired")}</p>
                ) : null}
                <Link href="/admin/system-base?section=phone" onClick={onConfigure} className="inline-flex min-h-9 w-fit items-center gap-1.5 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2">
                    <Settings2 className="h-4 w-4" aria-hidden="true" />{t("components.admin.DevicePairingPanel.configureAddress")}
                </Link>
            </div>
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                <div className="min-w-0 flex-1">
                    <div className="inline-flex max-w-full text-sm font-semibold text-foreground dark:text-slate-100">
                        <AdminHoverInfo content={t("components.admin.DevicePairingPanel.description")} panelClassName="text-xs leading-5">
                            <span>{t("components.admin.DevicePairingPanel.title")}</span>
                        </AdminHoverInfo>
                    </div>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <Button type="button" onClick={() => void createTicket()} disabled={busy || addressBusy || !manifest?.pairing?.available}>
                        {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <QrCode className="mr-2 h-4 w-4" />}
                        {t("components.admin.DevicePairingPanel.create")}
                    </Button>
                </div>
            </div>

            {error ? (
                <div role="alert" className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
                    {error}
                </div>
            ) : null}

            {ticket ? (
                <div className="mt-4 grid gap-3 rounded-lg border border-border bg-card p-3 sm:grid-cols-[160px_minmax(0,1fr)] sm:items-start dark:border-slate-800 dark:bg-slate-900">
                    <div className="flex min-h-40 items-center justify-center rounded-lg border border-border bg-card p-2 dark:border-slate-800 dark:bg-slate-950">
                        {qrDataUrl ? (
                            <Image src={qrDataUrl} alt={t("components.admin.DevicePairingPanel.qrAlt")} width={144} height={144} unoptimized />
                        ) : (
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                        )}
                    </div>
                    <div className="grid gap-3">
                        <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-3 dark:border-slate-800 dark:bg-slate-900">
                            <div className="text-sm font-semibold text-foreground dark:text-slate-100">
                                {t("components.admin.DevicePairingPanel.scanTitle")}
                            </div>
                            <div className="mt-1 text-sm leading-6 text-muted-foreground dark:text-slate-300">
                                {t("components.admin.DevicePairingPanel.scanHint")}
                            </div>
                        </div>
                        <Button type="button" variant="outline" onClick={() => void copyPairingUri()}>
                            {copied ? <Check className="mr-2 h-4 w-4" /> : <Clipboard className="mr-2 h-4 w-4" />}
                            {copied ? t("components.admin.DevicePairingPanel.copied") : t("components.admin.DevicePairingPanel.copyBackup")}
                        </Button>
                        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
                            <span>{t("components.admin.DevicePairingPanel.instance")}: {ticket.instanceId}</span>
                            <span>{t("components.admin.DevicePairingPanel.expires")}: {new Date(ticket.expiresAt).toLocaleTimeString()}</span>
                        </div>
                    </div>
                </div>
            ) : null}

            <div className="mt-5 border-t border-border pt-4 dark:border-slate-800">
                <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-foreground dark:text-slate-100">
                        {t("components.admin.DevicePairingPanel.connectedDevices")}
                    </div>
                    {devicesBusy ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                </div>
                {!devicesBusy && devices.length === 0 ? (
                    <div className="mt-2 text-sm text-muted-foreground">
                        {t("components.admin.DevicePairingPanel.noDevices")}
                    </div>
                ) : null}
                <div className="mt-2 grid gap-2">
                    {devices.map((device) => (
                        <div key={device.id} className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-medium text-foreground dark:text-slate-100">{device.deviceName}</div>
                                <div className="text-xs text-muted-foreground">
                                    {t("components.admin.DevicePairingPanel.connectedAt")}: {new Date(device.createdAt).toLocaleString()}
                                </div>
                            </div>
                            <Button type="button" variant="ghost" size="icon" onClick={() => void revokeDevice(device.id)} disabled={revokingId === device.id} title={t("components.admin.DevicePairingPanel.revoke")}>
                                {revokingId === device.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                            </Button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
