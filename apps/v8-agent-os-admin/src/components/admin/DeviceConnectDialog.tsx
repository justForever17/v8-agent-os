"use client";

import { useRef, useState } from "react";
import { QrCode } from "lucide-react";
import dynamic from "next/dynamic";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
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

const DevicePairingPanel = dynamic(() => import("@/components/admin/DevicePairingPanel").then((module) => module.DevicePairingPanel));

export function DeviceConnectDialog() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const contentRef = useRef<HTMLDivElement>(null);

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button
                    variant="ghost"
                    className="h-[40px] min-w-[40px] shrink-0 gap-2 px-2.5 text-foreground/80 hover:text-foreground [@media(pointer:coarse)]:min-h-[44px] [@media(pointer:coarse)]:min-w-[44px]"
                    aria-label={t("components.admin.DeviceConnectDialog.open")}
                    title={t("components.admin.DeviceConnectDialog.open")}
                >
                    <QrCode className="h-[18px] w-[18px]" aria-hidden="true" />
                    <span className="hidden text-xs sm:inline">{t("components.admin.DeviceConnectDialog.open")}</span>
                </Button>
            </DialogTrigger>
            <DialogContent ref={contentRef} tabIndex={-1} onOpenAutoFocus={(event) => {
                event.preventDefault();
                contentRef.current?.focus();
            }} className="max-h-[88vh] max-w-2xl overflow-y-auto rounded-lg border-border bg-card p-5">
                <DialogHeader>
                    <DialogTitle>
                        <AdminHoverInfo content={t("components.admin.DeviceConnectDialog.description")} panelClassName="text-xs leading-5">
                            <span>{t("components.admin.DeviceConnectDialog.title")}</span>
                        </AdminHoverInfo>
                    </DialogTitle>
                    <DialogDescription className="sr-only">{t("components.admin.DeviceConnectDialog.description")}</DialogDescription>
                </DialogHeader>

                <DevicePairingPanel onConfigure={() => setOpen(false)} />
            </DialogContent>
        </Dialog>
    );
}
