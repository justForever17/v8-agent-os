"use client";

import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { updateUserAvatar, updateUserNickname } from "@/lib/actions/user.actions";
import { resolveProfileAvatarSrc, useClientProfile } from "@/hooks/use-client-profile";
import { BackgroundPlaylistSettings } from "./BackgroundPlaylistSettings";
import { ImageUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useT } from "@/components/providers/LocaleProvider";
import { AvatarCropDialog } from "@/components/media/AvatarCropDialog";

interface SettingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
    const { profile, refreshProfile, applyProfile } = useClientProfile();
    const t = useT();
    const [nickname, setNickname] = useState(profile?.name || "");
    const [avatarUrl, setAvatarUrl] = useState(profile?.image || "");
    const [avatarCropFile, setAvatarCropFile] = useState<File | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);
    const avatarFileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        setNickname(profile?.name || "");
        setAvatarUrl(profile?.image || "");
    }, [profile?.appearance, profile?.image, profile?.name]);

    useEffect(() => {
        if (!open) return;
        void refreshProfile();
    }, [open, refreshProfile]);

    const handleUpdateNickname = async () => {
        setIsLoading(true);
        setMessage(null);
        try {
            const result = await updateUserNickname(nickname);
            if (result.success) {
                await applyProfile(result.user || { ...(profile || {}), name: nickname });
                setMessage({ type: 'success', text: t("web.generated.fc6c343534") });
            } else {
                setMessage({ type: 'error', text: result.error || t("web.generated.8e798b462a") });
            }
        } catch {
            setMessage({ type: 'error', text: t("web.generated.642978e9f3") });
        } finally {
            setIsLoading(false);
        }
    };

    const handleAvatarUpload = async (file: File) => {
        setIsLoading(true);
        setMessage(null);
        try {
            const formData = new FormData();
            formData.append("file", file);
            const response = await fetch("/api/user-avatar-upload", {
                method: "POST",
                body: formData,
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.url) {
                throw new Error(data.error || t("web.generated.6ba695e4a7"));
            }
            const nextImage = String(data.path || data.url);
            setAvatarUrl(nextImage);
            await applyProfile(data.user || { ...(profile || {}), image: nextImage });
            setMessage({ type: "success", text: t("web.generated.3b77e58de9") });
            return true;
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.348cb3f84a") });
            return false;
        } finally {
            setIsLoading(false);
        }
    };


    return (
        <>
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="v8-personalization-dialog max-h-[86dvh] gap-0 overflow-hidden p-0 sm:max-w-[720px]">
                <DialogHeader className="border-b px-6 pb-4 pt-5 text-left">
                    <DialogTitle>{t("web.generated.36b502ac91")}</DialogTitle>
                    <DialogDescription>
                        {t("web.generated.17ceff9267")}
                    </DialogDescription>
                </DialogHeader>

                <div className="min-h-0 space-y-4 overflow-y-auto px-6 py-4">
                    <section className="rounded-2xl border border-border/70 bg-card/55 p-4">
                        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-4">
                            <div className="flex flex-col items-center gap-2">
                                <Avatar className="h-20 w-20 border border-border/60 bg-muted text-2xl font-semibold text-muted-foreground shadow-sm">
                                    <AvatarImage
                                        src={resolveProfileAvatarSrc(avatarUrl)}
                                        alt={profile?.name || t("web.generated.154566d5fa")}
                                        className="object-cover"
                                    />
                                    <AvatarFallback>{profile?.name?.charAt(0).toUpperCase() || "U"}</AvatarFallback>
                                </Avatar>
                                <input
                                    ref={avatarFileInputRef}
                                    id="user-avatar-upload"
                                    type="file"
                                    accept="image/*"
                                    className="hidden"
                                    onChange={(event) => {
                                        const file = event.target.files?.[0];
                                        if (file) setAvatarCropFile(file);
                                        event.target.value = "";
                                    }}
                                />
                            </div>

                            <div className="min-w-0 space-y-2">
                                <div className="space-y-1.5">
                                    <Label htmlFor="nickname">{t("web.generated.a04cc6036e")}</Label>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Input
                                            id="nickname"
                                            className="min-w-[180px] flex-1"
                                            value={nickname}
                                            onChange={(e) => setNickname(e.target.value)}
                                            placeholder={t("web.generated.14f7afc6d0")}
                                        />
                                        <Button className="h-8 px-3 text-xs" onClick={handleUpdateNickname} disabled={isLoading}>
                                            {t("web.generated.5fe30f30dd")}
                                        </Button>
                                        <Button
                                            type="button"
                                            className="h-8 px-3 text-xs"
                                            variant="outline"
                                            disabled={isLoading}
                                            onClick={() => avatarFileInputRef.current?.click()}
                                        >
                                            <ImageUp className="mr-1.5 h-3.5 w-3.5" />
                                            {t("web.generated.8f6f49e2f3")}
                                        </Button>
                                        {avatarUrl ? (
                                            <Button
                                                type="button"
                                                className="h-8 px-2.5 text-xs"
                                                variant="ghost"
                                                disabled={isLoading}
                                                onClick={async () => {
                                                    const result = await updateUserAvatar("");
                                                    if (result.success) {
                                                        setAvatarUrl("");
                                                        await applyProfile(result.user || { ...(profile || {}), image: "" });
                                                        setMessage({ type: "success", text: t("web.generated.aeba374eff") });
                                                    } else {
                                                        setMessage({ type: "error", text: result.error || t("web.generated.3dea3ec94c") });
                                                    }
                                                }}
                                            >
                                                {t("web.generated.4e3b7ce293")}
                                            </Button>
                                        ) : null}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </section>

                    <BackgroundPlaylistSettings open={open} />

                    {message && (
                        <div className={`rounded-lg p-2 text-sm ${message.type === 'success' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
                            {message.text}
                        </div>
                    )}
                </div>

                <DialogFooter className="border-t px-6 py-3">
                    <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>{t("web.generated.fbd8cee012")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
        <AvatarCropDialog
            file={avatarCropFile}
            busy={isLoading}
            onCancel={() => setAvatarCropFile(null)}
            onConfirm={async (file) => {
                if (await handleAvatarUpload(file)) setAvatarCropFile(null);
            }}
        />
        </>
    );
}
