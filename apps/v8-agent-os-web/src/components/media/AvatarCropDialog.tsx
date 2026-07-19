"use client";

import { SquareImageCropper } from "@v8/product-ui";

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/components/providers/LocaleProvider";

type AvatarCropDialogProps = {
    file: File | null;
    busy?: boolean;
    onCancel: () => void;
    onConfirm: (file: File) => void | Promise<void>;
};

export function AvatarCropDialog({ file, busy = false, onCancel, onConfirm }: AvatarCropDialogProps) {
    const t = useT();
    return (
        <Dialog open={Boolean(file)} onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
            <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-[520px]">
                <DialogHeader>
                    <DialogTitle>{t("web.avatarCrop.title")}</DialogTitle>
                    <DialogDescription>{t("web.avatarCrop.description")}</DialogDescription>
                </DialogHeader>
                {file ? (
                    <SquareImageCropper
                        file={file}
                        busy={busy}
                        labels={{
                            cropArea: t("web.avatarCrop.area"),
                            instruction: t("web.avatarCrop.instruction"),
                            zoom: t("web.avatarCrop.zoom"),
                            zoomOut: t("web.avatarCrop.zoomOut"),
                            zoomIn: t("web.avatarCrop.zoomIn"),
                            cancel: t("web.avatarCrop.cancel"),
                            confirm: t("web.avatarCrop.confirm"),
                        }}
                        onCancel={onCancel}
                        onConfirm={onConfirm}
                    />
                ) : null}
            </DialogContent>
        </Dialog>
    );
}
