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
          <DialogTitle>{t("admin.avatarCrop.title")}</DialogTitle>
          <DialogDescription>{t("admin.avatarCrop.description")}</DialogDescription>
        </DialogHeader>
        {file ? (
          <SquareImageCropper
            file={file}
            busy={busy}
            labels={{
              cropArea: t("admin.avatarCrop.area"),
              instruction: t("admin.avatarCrop.instruction"),
              zoom: t("admin.avatarCrop.zoom"),
              zoomOut: t("admin.avatarCrop.zoomOut"),
              zoomIn: t("admin.avatarCrop.zoomIn"),
              cancel: t("admin.avatarCrop.cancel"),
              confirm: t("admin.avatarCrop.confirm"),
            }}
            onCancel={onCancel}
            onConfirm={onConfirm}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
