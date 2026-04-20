"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";

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
            setError(t("components.admin.AdminPasswordGate.k0809b07d"));
            return;
        }
        if (newPassword !== confirmPassword) {
            setError(t("components.admin.AdminPasswordGate.kc494ae80"));
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
                throw new Error(String(data.error || t("components.admin.AdminPasswordGate.k726c977b")));
            }
            setNewPassword("");
            setConfirmPassword("");
            await update({ mustChangePassword: false });
            window.location.reload();
        } catch (err) {
            setError(err instanceof Error ? err.message : t("components.admin.AdminPasswordGate.k726c977b"));
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
                    <DialogTitle>{t("components.admin.AdminPasswordGate.ked311bc6")}</DialogTitle>
                    <DialogDescription>
                        {t("components.admin.AdminPasswordGate.kfabd25c5")}
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label htmlFor="admin-new-password">{t("components.admin.AdminPasswordGate.kda6842bf")}</Label>
                        <Input
                            id="admin-new-password"
                            type="password"
                            autoComplete="new-password"
                            value={newPassword}
                            onChange={(event) => setNewPassword(event.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="admin-confirm-password">{t("components.admin.AdminPasswordGate.k641b208a")}</Label>
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
                        {saving ? t("components.admin.AdminPasswordGate.kc225e8a3") : t("components.admin.AdminPasswordGate.k74f40a86")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
