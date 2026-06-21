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
import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import { ThemeToggle } from "../layout/ThemeToggle";
import { AdminConnectionManager } from "@/components/connection/AdminConnectionManager";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface SettingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
    const { data: session, update } = useSession();
    const t = useT();
    const [nickname, setNickname] = useState(session?.user?.name || "");
    const [avatarUrl, setAvatarUrl] = useState(session?.user?.image || "");
    const [customAvatarUrl, setCustomAvatarUrl] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

    useEffect(() => {
        setNickname(session?.user?.name || "");
        setAvatarUrl(session?.user?.image || "");
    }, [session?.user?.image, session?.user?.name]);

    const handleUpdateNickname = async () => {
        setIsLoading(true);
        setMessage(null);
        try {
            const result = await updateUserNickname(nickname);
            if (result.success) {
                await update({ name: nickname });
                setMessage({ type: 'success', text: t(lt("昵称更新成功", "Display name updated")) });
            } else {
                setMessage({ type: 'error', text: result.error || t(lt("更新失败", "Update failed")) });
            }
        } catch {
            setMessage({ type: 'error', text: t(lt("发生错误", "Something went wrong")) });
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
                throw new Error(data.error || t(lt("头像上传失败", "Avatar upload failed")));
            }
            const result = await updateUserAvatar(String(data.url));
            if (!result.success) {
                throw new Error(result.error || t(lt("头像保存失败", "Failed to save avatar")));
            }
            setAvatarUrl(String(data.url));
            await update({ image: String(data.url) });
            setMessage({ type: "success", text: t(lt("头像更新成功", "Avatar updated")) });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t(lt("头像更新失败", "Avatar update failed")) });
        } finally {
            setIsLoading(false);
        }
    };

    const handleAvatarUrlSave = async () => {
        if (!customAvatarUrl.trim()) {
            setMessage({ type: "error", text: t(lt("请输入头像地址", "Please enter an avatar URL")) });
            return;
        }
        setIsLoading(true);
        setMessage(null);
        try {
            const nextUrl = customAvatarUrl.trim();
            const result = await updateUserAvatar(nextUrl);
            if (!result.success) {
                throw new Error(result.error || t(lt("头像保存失败", "Failed to save avatar")));
            }
            setAvatarUrl(nextUrl);
            await update({ image: nextUrl });
            setCustomAvatarUrl("");
            setMessage({ type: "success", text: t(lt("头像更新成功", "Avatar updated")) });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t(lt("头像更新失败", "Avatar update failed")) });
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t(lt("设置", "Settings"))}</DialogTitle>
                    <DialogDescription>
                        {t(lt("管理您的个人资料和应用偏好。", "Manage your profile and app preferences."))}
                    </DialogDescription>
                </DialogHeader>

                <Tabs defaultValue="profile" className="w-full">
                    <TabsList className="grid w-full grid-cols-2">
                        <TabsTrigger value="profile">{t(lt("聊天资料", "Chat profile"))}</TabsTrigger>
                        <TabsTrigger value="connection">{t(lt("连接管理", "Connection"))}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="profile" className="space-y-4 py-4">
                        <div className="flex items-center gap-4">
                            <div className="h-16 w-16 rounded-full bg-muted flex items-center justify-center overflow-hidden text-2xl font-semibold text-muted-foreground">
                                {avatarUrl ? (
                                    <Image
                                        src={avatarUrl}
                                        alt={session?.user?.name || t(lt("用户头像", "User avatar"))}
                                        width={64}
                                        height={64}
                                        className="h-full w-full object-cover"
                                        unoptimized
                                    />
                                ) : (session?.user?.name?.charAt(0).toUpperCase() || "U")}
                            </div>
                            <div className="flex-1 space-y-3">
                                <Label htmlFor="nickname">{t(lt("昵称", "Display name"))}</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="nickname"
                                        value={nickname}
                                        onChange={(e) => setNickname(e.target.value)}
                                        placeholder={t(lt("输入新昵称", "Enter a new display name"))}
                                    />
                                    <Button onClick={handleUpdateNickname} disabled={isLoading}>
                                        {t(lt("保存", "Save"))}
                                    </Button>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="user-avatar-upload">{t(lt("头像", "Avatar"))}</Label>
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
                                                placeholder={t(lt("或输入头像图片地址", "Or enter an avatar image URL"))}
                                            />
                                            <Button onClick={handleAvatarUrlSave} disabled={isLoading}>
                                                {t(lt("保存头像", "Save avatar"))}
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
                                                        await update({ image: "" });
                                                        setMessage({ type: "success", text: t(lt("已恢复默认头像", "Default avatar restored")) });
                                                    } else {
                                                        setMessage({ type: "error", text: result.error || t(lt("恢复失败", "Restore failed")) });
                                                    }
                                                }}
                                            >
                                                {t(lt("清空头像", "Clear avatar"))}
                                            </Button>
                                        ) : null}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-2 pt-4 border-t">
                            <Label>{t(lt("外观主题", "Theme"))}</Label>
                            <div className="flex items-center justify-between p-2 border rounded-lg">
                                <span className="text-sm text-muted-foreground">{t(lt("切换深色/浅色模式", "Switch light and dark mode"))}</span>
                                <ThemeToggle />
                            </div>
                        </div>
                    </TabsContent>

                    <TabsContent value="connection" className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>{t(lt("管理台连接档案", "Admin connection profiles"))}</Label>
                            <p className="text-sm leading-6 text-muted-foreground">
                                {t(lt("这里会在当前设备上保存管理台档案，退出登录不会清掉它们。", "Admin console profiles stay on this device after sign-out."))}
                            </p>
                        </div>
                        <AdminConnectionManager variant="panel" />
                    </TabsContent>
                </Tabs>

                {message && (
                    <div className={`p-2 rounded text-sm ${message.type === 'success' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
                        {message.text}
                    </div>
                )}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>{t(lt("关闭", "Close"))}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
