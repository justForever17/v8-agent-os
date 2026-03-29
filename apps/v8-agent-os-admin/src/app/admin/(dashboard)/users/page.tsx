import { getUsers, createUser, deleteUser } from "@/lib/actions/users";
import { cookies } from "next/headers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { EditUserDialog } from "@/components/admin/EditUserDialog";
import { localizeAdminText } from "@/lib/admin-copy";
import { parseLocale } from "@/lib/locale";

// ... imports ...

export default async function UsersPage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = (value: string) => localizeAdminText(locale, value);
    const users = await getUsers();

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <h1 className="text-3xl font-bold tracking-tight">{t("用户管理")}</h1>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>{t("新建用户")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <form action={createUser} className="grid gap-4 lg:grid-cols-5">
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="login" className="text-sm font-medium">{t("登录名")}</label>
                            <Input type="text" id="login" name="login" placeholder={t("例如：admin")} required />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="name" className="text-sm font-medium">{t("昵称")}</label>
                            <Input type="text" id="name" name="name" placeholder={t("例如：管理员")} />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="password" className="text-sm font-medium">{t("初始密码")}</label>
                            <Input type="password" id="password" name="password" placeholder={t("至少 6 位")} required />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="role" className="text-sm font-medium">{t("角色")}</label>
                            <select
                                id="role"
                                name="role"
                                className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                <option value="USER">{t("普通用户")}</option>
                                <option value="ADMIN">{t("管理员")}</option>
                            </select>
                        </div>
                        <div className="flex items-center gap-3">
                            <label className="flex items-center gap-2 text-sm text-muted-foreground">
                                <input type="checkbox" name="mustChangePassword" defaultChecked className="h-4 w-4 rounded border-input" />
                                {t("下次登录要求改密")}
                            </label>
                            <Button type="submit">{t("创建用户")}</Button>
                        </div>
                    </form>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>{t("用户列表")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>{t("昵称")}</TableHead>
                                <TableHead>{t("登录名")}</TableHead>
                                <TableHead>{t("角色")}</TableHead>
                                <TableHead>{t("改密")}</TableHead>
                                <TableHead>{t("创建时间")}</TableHead>
                                <TableHead className="text-right">{t("操作")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {users.map((user) => (
                                <TableRow key={user.id}>
                                    <TableCell className="font-medium">{user.name || "N/A"}</TableCell>
                                    <TableCell>{user.login}</TableCell>
                                    <TableCell>
                                        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${user.role === 'ADMIN' ? 'bg-purple-100 text-purple-800' : 'bg-gray-100 text-gray-800'}`}>
                                            {user.role === 'ADMIN' ? t("管理员") : t("普通用户")}
                                        </span>
                                    </TableCell>
                                    <TableCell>{user.mustChangePassword ? t("需要") : t("无需")}</TableCell>
                                    <TableCell>{user.createdAt ? new Date(user.createdAt).toLocaleDateString() : 'N/A'}</TableCell>
                                    <TableCell className="text-right flex justify-end items-center">
                                        <EditUserDialog user={user as { id: string; name: string | null; login: string; role: "ADMIN" | "USER"; mustChangePassword?: boolean }} />
                                        <form action={deleteUser.bind(null, user.id as string)}>
                                            <Button variant="destructive" size="sm">{t("删除")}</Button>
                                        </form>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}
