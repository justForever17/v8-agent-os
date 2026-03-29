"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function AdminPasswordGate() {
    const t = useT();
    const { data: session, update } = useSession();
    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);

    const open = Boolean(session?.user?.mustChangePassword);

    const handleSubmit = async () => {
        if (!newPassword || newPassword.length < 6) {
            setError(t(lt("新密码至少需要 6 位", "Your new password must be at least 6 characters.")));
            return;
        }
        if (newPassword !== confirmPassword) {
            setError(t(lt("两次输入的密码不一致", "The passwords do not match.")));
            return;
        }

        setSaving(true);
        setError("");
        try {
            const response = await fetch("/api/auth/password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    newPassword,
                    forceMode: true,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(data.error || t(lt("密码更新失败", "Password update failed."))));
            }
            setNewPassword("");
            setConfirmPassword("");
            await update({ mustChangePassword: false });
            window.location.reload();
        } catch (err) {
            setError(err instanceof Error ? err.message : t(lt("密码更新失败", "Password update failed.")));
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open}>
            <DialogContent
                showCloseButton={false}
                onInteractOutside={(event) => event.preventDefault()}
                onEscapeKeyDown={(event) => event.preventDefault()}
                className="sm:max-w-md"
            >
                <DialogHeader>
                    <DialogTitle>{t(lt("请先修改管理员密码", "Update the admin password first"))}</DialogTitle>
                    <DialogDescription>
                        {t(lt("先完成密码更新，再继续使用后台配置页。", "Finish updating the password before continuing to the admin console."))}
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label htmlFor="admin-new-password">{t(lt("新密码", "New password"))}</Label>
                        <Input
                            id="admin-new-password"
                            type="password"
                            autoComplete="new-password"
                            value={newPassword}
                            onChange={(event) => setNewPassword(event.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="admin-confirm-password">{t(lt("确认密码", "Confirm password"))}</Label>
                        <Input
                            id="admin-confirm-password"
                            type="password"
                            autoComplete="new-password"
                            value={confirmPassword}
                            onChange={(event) => setConfirmPassword(event.target.value)}
                        />
                    </div>
                    {error ? <div className="text-sm text-rose-600">{error}</div> : null}
                </div>
                <DialogFooter>
                    <Button onClick={() => void handleSubmit()} disabled={saving}>
                        {saving ? t(lt("保存中...", "Saving...")) : t(lt("保存并进入后台", "Save and continue"))}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
