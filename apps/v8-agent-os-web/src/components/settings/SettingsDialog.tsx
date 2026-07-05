"use client";

import Image from "next/image";
import { Button } from "@/components/ui/button";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { updateUserAvatar, updateUserNickname } from "@/lib/actions/user.actions";
import { resolveProfileAvatarSrc, useClientProfile } from "@/hooks/use-client-profile";
import { useEffect, useState } from "react";
import { ThemeToggle } from "../layout/ThemeToggle";
import { useT } from "@/components/providers/LocaleProvider";

interface SettingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
    const { profile, refreshProfile, applyProfile } = useClientProfile();
    const t = useT();
    const [nickname, setNickname] = useState(profile?.name || "");
    const [avatarUrl, setAvatarUrl] = useState(profile?.image || "");
    const [customAvatarUrl, setCustomAvatarUrl] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

    useEffect(() => {
        setNickname(profile?.name || "");
        setAvatarUrl(profile?.image || "");
    }, [profile?.image, profile?.name]);

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
            const result = await updateUserAvatar(nextImage);
            if (!result.success) {
                throw new Error(result.error || t("web.generated.18142c77e9"));
            }
            setAvatarUrl(nextImage);
            await applyProfile(result.user || { ...(profile || {}), image: nextImage });
            setMessage({ type: "success", text: t("web.generated.3b77e58de9") });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.348cb3f84a") });
        } finally {
            setIsLoading(false);
        }
    };

    const handleAvatarUrlSave = async () => {
        if (!customAvatarUrl.trim()) {
            setMessage({ type: "error", text: t("web.generated.0381b828e4") });
            return;
        }
        setIsLoading(true);
        setMessage(null);
        try {
            const nextUrl = customAvatarUrl.trim();
            const result = await updateUserAvatar(nextUrl);
            if (!result.success) {
                throw new Error(result.error || t("web.generated.18142c77e9"));
            }
            setAvatarUrl(nextUrl);
            await applyProfile(result.user || { ...(profile || {}), image: nextUrl });
            setCustomAvatarUrl("");
            setMessage({ type: "success", text: t("web.generated.3b77e58de9") });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.348cb3f84a") });
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t("web.generated.36b502ac91")}</DialogTitle>
                    <DialogDescription>
                        {t("web.generated.17ceff9267")}
                    </DialogDescription>
                </DialogHeader>

                <Tabs defaultValue="profile" className="w-full">
                    <TabsList className="grid w-full grid-cols-1">
                        <TabsTrigger value="profile">{t("web.generated.0f5a6e0b58")}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="profile" className="space-y-4 py-4">
                        <div className="flex items-center gap-4">
                            <div className="h-16 w-16 rounded-full bg-muted flex items-center justify-center overflow-hidden text-2xl font-semibold text-muted-foreground">
                                {avatarUrl ? (
                                    <Image
                                        src={resolveProfileAvatarSrc(avatarUrl)}
                                        alt={profile?.name || t("web.generated.154566d5fa")}
                                        width={64}
                                        height={64}
                                        className="h-full w-full object-cover"
                                        unoptimized
                                    />
                                ) : (profile?.name?.charAt(0).toUpperCase() || "U")}
                            </div>
                            <div className="flex-1 space-y-3">
                                <Label htmlFor="nickname">{t("web.generated.a04cc6036e")}</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="nickname"
                                        value={nickname}
                                        onChange={(e) => setNickname(e.target.value)}
                                        placeholder={t("web.generated.14f7afc6d0")}
                                    />
                                    <Button onClick={handleUpdateNickname} disabled={isLoading}>
                                        {t("web.generated.5fe30f30dd")}
                                    </Button>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="user-avatar-upload">{t("web.generated.8f6f49e2f3")}</Label>
                                    <div className="flex flex-wrap gap-2">
                                        <Input
                                            id="user-avatar-upload"
                                            type="file"
                                            accept="image/*"
                                            onChange={(event) => {
                                                const file = event.target.files?.[0];
                                                if (file) {
                                                    void handleAvatarUpload(file);
                                                    event.target.value = "";
                                                }
                                            }}
                                        />
                                        <div className="flex w-full gap-2">
                                            <Input
                                                value={customAvatarUrl}
                                                onChange={(event) => setCustomAvatarUrl(event.target.value)}
                                                placeholder={t("web.generated.df178badba")}
                                            />
                                            <Button onClick={handleAvatarUrlSave} disabled={isLoading}>
                                                {t("web.generated.4a0177af0a")}
                                            </Button>
                                        </div>
                                        {avatarUrl ? (
                                            <Button
                                                type="button"
                                                variant="outline"
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

                        <div className="space-y-2 pt-4 border-t">
                            <Label>{t("web.generated.2d9d54c63a")}</Label>
                            <div className="flex items-center justify-between p-2 border rounded-lg">
                                <span className="text-sm text-muted-foreground">{t("web.generated.98ed00c0e4")}</span>
                                <ThemeToggle />
                            </div>
                        </div>
                    </TabsContent>

                </Tabs>

                {message && (
                    <div className={`p-2 rounded text-sm ${message.type === 'success' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
                        {message.text}
                    </div>
                )}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>{t("web.generated.fbd8cee012")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
