"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";

import { updateUserPassword } from "@/lib/actions/user.actions";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function WebPasswordGate() {
    const t = useT();
    const { data: session, update } = useSession();
    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

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
            const result = await updateUserPassword("", newPassword, true);
            if (!result.success) {
                throw new Error(result.error || t(lt("密码更新失败", "Password update failed.")));
            }
            await update({ mustChangePassword: false });
            setNewPassword("");
            setConfirmPassword("");
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
                    <DialogTitle>{t(lt("请先修改登录密码", "Update your password first"))}</DialogTitle>
                    <DialogDescription>
                        {t(lt("完成密码更新后，才能继续使用当前账号。", "Finish updating the password before continuing with this account."))}
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label htmlFor="web-new-password">{t(lt("新密码", "New password"))}</Label>
                        <Input
                            id="web-new-password"
                            type="password"
                            autoComplete="new-password"
                            value={newPassword}
                            onChange={(event) => setNewPassword(event.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="web-confirm-password">{t(lt("确认密码", "Confirm password"))}</Label>
                        <Input
                            id="web-confirm-password"
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
                        {saving ? t(lt("保存中...", "Saving...")) : t(lt("保存并继续", "Save and continue"))}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
