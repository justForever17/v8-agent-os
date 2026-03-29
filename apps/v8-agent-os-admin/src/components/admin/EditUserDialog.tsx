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
                    <DialogTitle>{t("编辑用户")}</DialogTitle>
                    <DialogDescription>
                        {t("修改用户信息和权限。")}
                    </DialogDescription>
                </DialogHeader>
                <form action={async (formData) => {
                    await updateUser(formData);
                    setOpen(false);
                }}>
                    <input type="hidden" name="id" value={user.id} />
                    <div className="grid gap-4 py-4">
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="name" className="text-right">
                                {t("昵称")}
                            </Label>
                            <Input id="name" name="name" defaultValue={user.name || ""} className="col-span-3" />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="login" className="text-right">
                                {t("登录名")}
                            </Label>
                            <Input id="login" name="login" defaultValue={user.login} className="col-span-3" />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="role" className="text-right">
                                {t("角色")}
                            </Label>
                            <select
                                id="role"
                                name="role"
                                defaultValue={user.role}
                                className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 col-span-3"
                            >
                                <option value="USER">{t("普通用户")}</option>
                                <option value="ADMIN">{t("管理员")}</option>
                            </select>
                        </div>
                        <div className="grid grid-cols-4 items-start gap-4">
                            <Label htmlFor="resetPassword" className="pt-2 text-right">
                                {t("重置密码")}
                            </Label>
                            <div className="col-span-3 space-y-2">
                                <Input id="resetPassword" name="resetPassword" type="password" placeholder={t("留空表示不修改")} />
                                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <input
                                        type="checkbox"
                                        name="mustChangePassword"
                                        defaultChecked={Boolean(user.mustChangePassword)}
                                        className="h-4 w-4 rounded border-input"
                                    />
                                    {t("下次登录要求改密")}
                                </label>
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button type="submit">{t("保存修改")}</Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
