"use client";

import { useState } from "react";
import { Check, Clipboard, Loader2, QrCode } from "lucide-react";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { DevicePairingPanel } from "@/components/admin/DevicePairingPanel";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { useT } from "@/components/providers/LocaleProvider";

type InstanceManifest = {
    instanceId?: string;
    admin?: { baseUrl?: string };
    warnings?: string[];
};

export function DeviceConnectDialog() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [manifest, setManifest] = useState<InstanceManifest | null>(null);
    const [copied, setCopied] = useState(false);
    const browserOrigin = typeof window === "undefined" ? "" : window.location.origin;

    async function loadManifest() {
        setLoading(true);
        try {
            const response = await fetch("/api/client/instance", { cache: "no-store" });
            setManifest(await response.json() as InstanceManifest);
        } catch {
            setManifest({ admin: { baseUrl: browserOrigin } });
        } finally {
            setLoading(false);
        }
    }

    async function copyAdminUrl() {
        const adminBaseUrl = String(manifest?.admin?.baseUrl || browserOrigin);
        await navigator.clipboard.writeText(adminBaseUrl);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
    }

    return (
        <Dialog open={open} onOpenChange={(nextOpen) => {
            setOpen(nextOpen);
            if (nextOpen && !manifest && !loading) {
                void loadManifest();
            }
        }}>
            <DialogTrigger asChild>
                <Button
                    variant="outline"
                    size="icon"
                    className="h-[25px] w-[25px] rounded-lg border-slate-200 bg-white p-0 text-slate-500"
                    aria-label={t("components.admin.DeviceConnectDialog.open")}
                    title={t("components.admin.DeviceConnectDialog.open")}
                >
                    <QrCode className="h-3.5 w-3.5" />
                </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto rounded-lg border-slate-200 bg-white p-5">
                <DialogHeader>
                    <DialogTitle>
                        <AdminHoverInfo content={t("components.admin.DeviceConnectDialog.description")} panelClassName="text-xs leading-5">
                            <span>{t("components.admin.DeviceConnectDialog.title")}</span>
                        </AdminHoverInfo>
                    </DialogTitle>
                    <DialogDescription className="sr-only">{t("components.admin.DeviceConnectDialog.description")}</DialogDescription>
                </DialogHeader>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="text-xs font-medium text-slate-500">
                        {t("components.admin.DeviceConnectDialog.adminUrl")}
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                        <div className="min-w-0 flex-1 break-all font-mono text-sm text-slate-800">
                            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : manifest?.admin?.baseUrl || browserOrigin || "-"}
                        </div>
                        <Button type="button" variant="outline" size="sm" onClick={() => void copyAdminUrl()}>
                            {copied ? <Check className="mr-2 h-4 w-4" /> : <Clipboard className="mr-2 h-4 w-4" />}
                            {copied ? t("components.admin.DeviceConnectDialog.copied") : t("components.admin.DeviceConnectDialog.copy")}
                        </Button>
                    </div>
                    {manifest?.instanceId ? (
                        <div className="mt-2 text-xs text-slate-500">
                            {t("components.admin.DeviceConnectDialog.instance")}: {manifest.instanceId}
                        </div>
                    ) : null}
                    {manifest?.warnings?.includes("admin_loopback_not_reachable_from_phone") ? (
                        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-5 text-amber-800">
                            {t("components.admin.DeviceConnectDialog.loopbackWarning")}
                        </div>
                    ) : null}
                </div>

                <DevicePairingPanel />
            </DialogContent>
        </Dialog>
    );
}
