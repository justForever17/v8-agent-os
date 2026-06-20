"use client";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { updateUser } from "@/lib/actions/users";
import { Pencil } from "lucide-react";
import { useState } from "react";
import { useT } from "@/components/providers/LocaleProvider";

interface User {
    id: string;
    name: string | null;
    login: string;
    role: "USER" | "ADMIN";
    mustChangePassword?: boolean;
}

export function EditUserDialog({ user }: { user: User }) {
    const t = useT();
    const [open, setOpen] = useState(false);

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="mr-2">
                    <Pencil className="w-4 h-4" />
                </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[425px]">
                <DialogHeader>
                    <DialogTitle>{t("components.admin.EditUserDialog.k667bc756")}</DialogTitle>
                    <DialogDescription>
                        {t("components.admin.EditUserDialog.k58adc925")}
                    </DialogDescription>
                </DialogHeader>
                <form action={async (formData) => {
                    await updateUser(formData);
                    setOpen(false);
                }}>
                    <input type="hidden" name="id" value={user.id} />
                    <input type="hidden" name="role" value="ADMIN" />
                    <div className="grid gap-4 py-4">
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="name" className="text-right">
                                {t("components.admin.EditUserDialog.kb6f6dc96")}
                            </Label>
                            <Input id="name" name="name" defaultValue={user.name || ""} className="col-span-3" />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="login" className="text-right">
                                {t("components.admin.EditUserDialog.k27ba7ff8")}
                            </Label>
                            <Input id="login" name="login" defaultValue={user.login} className="col-span-3" />
                        </div>
                        <div className="grid grid-cols-4 items-start gap-4">
                            <Label htmlFor="resetPassword" className="pt-2 text-right">
                                {t("components.admin.EditUserDialog.k91c072e8")}
                            </Label>
                            <div className="col-span-3 space-y-2">
                                <Input id="resetPassword" name="resetPassword" type="password" placeholder={t("components.admin.EditUserDialog.ka8691ef0")} />
                                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <input
                                        type="checkbox"
                                        name="mustChangePassword"
                                        defaultChecked={Boolean(user.mustChangePassword)}
                                        className="h-4 w-4 rounded border-input"
                                    />
                                    {t("components.admin.EditUserDialog.kc50b82ec")}
                                </label>
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button type="submit">{t("components.admin.EditUserDialog.k1a3a9893")}</Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
