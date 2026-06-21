"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Clipboard, Link2, Loader2, Trash2 } from "lucide-react";
import Image from "next/image";
import QRCode from "qrcode";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

type PairingSurface = "phone" | "cyber" | "web" | "custom";

type PairingTicket = {
    pairingId: string;
    instanceId: string;
    surface: PairingSurface;
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

export function DevicePairingPanel() {
    const t = useT();
    const [surface, setSurface] = useState<PairingSurface>("phone");
    const [ticket, setTicket] = useState<PairingTicket | null>(null);
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState("");
    const [devices, setDevices] = useState<DeviceSession[]>([]);
    const [devicesBusy, setDevicesBusy] = useState(true);
    const [revokingId, setRevokingId] = useState("");
    const [qrDataUrl, setQrDataUrl] = useState("");

    const loadDevices = useCallback(async () => {
        setDevicesBusy(true);
        try {
            const response = await fetch("/api/client/devices", { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            setDevices(response.ok && Array.isArray(payload?.devices) ? payload.devices : []);
        } catch {
            setDevices([]);
        } finally {
            setDevicesBusy(false);
        }
    }, []);

    useEffect(() => {
        void loadDevices();
    }, [loadDevices]);

    useEffect(() => {
        if (!ticket?.pairingUri || ticket.surface !== "phone") {
            setQrDataUrl("");
            return;
        }
        let cancelled = false;
        QRCode.toDataURL(ticket.pairingUri, {
            width: 240,
            margin: 1,
            errorCorrectionLevel: "M",
            color: { dark: "#0f172a", light: "#ffffff" },
        }).then((value) => {
            if (!cancelled) setQrDataUrl(value);
        }).catch(() => {
            if (!cancelled) setQrDataUrl("");
        });
        return () => {
            cancelled = true;
        };
    }, [ticket]);

    async function createTicket() {
        setBusy(true);
        setError("");
        setCopied(false);
        try {
            const response = await fetch("/api/client/pairing/tickets", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    surface,
                    deviceName: `v8-${surface}`,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload?.pairingCode || !payload?.pairingUri) {
                throw new Error(String(payload?.error || t("components.admin.DevicePairingPanel.createFailed")));
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
        await navigator.clipboard.writeText(ticket.pairingUri);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
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
            await loadDevices();
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t("components.admin.DevicePairingPanel.revokeFailed"));
        } finally {
            setRevokingId("");
        }
    }

    return (
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-slate-950/40">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                <div className="min-w-0 flex-1">
                    <div className="inline-flex max-w-full text-sm font-semibold text-slate-900 dark:text-slate-100">
                        <AdminHoverInfo content={t("components.admin.DevicePairingPanel.description")} panelClassName="text-xs leading-5">
                            <span>{t("components.admin.DevicePairingPanel.title")}</span>
                        </AdminHoverInfo>
                    </div>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <select
                        value={surface}
                        onChange={(event) => setSurface(event.target.value as PairingSurface)}
                        className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                    >
                        <option value="phone">Phone</option>
                        <option value="cyber">CyberCore</option>
                        <option value="web">Web</option>
                        <option value="custom">{t("components.admin.DevicePairingPanel.customClient")}</option>
                    </select>
                    <Button type="button" onClick={() => void createTicket()} disabled={busy}>
                        {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Link2 className="mr-2 h-4 w-4" />}
                        {t("components.admin.DevicePairingPanel.create")}
                    </Button>
                </div>
            </div>

            {error ? (
                <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
                    {error}
                </div>
            ) : null}

            {ticket ? (
                <div className={`mt-4 grid gap-3 ${ticket.surface === "phone" ? "sm:grid-cols-[minmax(0,1fr)_160px] sm:items-start" : ""}`}>
                    <div className="grid gap-3">
                        <div className="min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-3 dark:border-slate-800 dark:bg-slate-900">
                            <div className="text-xs text-slate-500">{t("components.admin.DevicePairingPanel.link")}</div>
                            <div className="mt-1 break-all font-mono text-xs leading-5 text-slate-800 dark:text-slate-200">
                                {ticket.pairingUri}
                            </div>
                        </div>
                        <Button type="button" variant="outline" onClick={() => void copyPairingUri()}>
                            {copied ? <Check className="mr-2 h-4 w-4" /> : <Clipboard className="mr-2 h-4 w-4" />}
                            {copied ? t("components.admin.DevicePairingPanel.copied") : t("components.admin.DevicePairingPanel.copy")}
                        </Button>
                        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500">
                            <span>{t("components.admin.DevicePairingPanel.instance")}: {ticket.instanceId}</span>
                            <span>{t("components.admin.DevicePairingPanel.expires")}: {new Date(ticket.expiresAt).toLocaleTimeString()}</span>
                        </div>
                    </div>
                    {ticket.surface === "phone" ? (
                        <div className="flex min-h-40 items-center justify-center rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-900">
                            {qrDataUrl ? (
                                <Image src={qrDataUrl} alt={t("components.admin.DevicePairingPanel.qrAlt")} width={144} height={144} unoptimized />
                            ) : (
                                <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                            )}
                        </div>
                    ) : null}
                </div>
            ) : null}

            <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-800">
                <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        {t("components.admin.DevicePairingPanel.connectedDevices")}
                    </div>
                    {devicesBusy ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                </div>
                {!devicesBusy && devices.length === 0 ? (
                    <div className="mt-2 text-sm text-slate-500">
                        {t("components.admin.DevicePairingPanel.noDevices")}
                    </div>
                ) : null}
                <div className="mt-2 grid gap-2">
                    {devices.map((device) => (
                        <div key={device.id} className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">{device.deviceName}</div>
                                <div className="text-xs text-slate-500">
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
