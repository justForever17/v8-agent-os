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
import { createTranslator, parseLocale } from "@/lib/locale";

// ... imports ...

export default async function UsersPage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = createTranslator(locale);
    const users = await getUsers();

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <h1 className="text-3xl font-bold tracking-tight">{t("app.admin.dashboard.users.page.k686a015c")}</h1>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>{t("app.admin.dashboard.users.page.k80e8c9c4")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <form action={createUser} className="grid gap-4 lg:grid-cols-5">
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="login" className="text-sm font-medium">{t("app.admin.dashboard.users.page.k27ba7ff8")}</label>
                            <Input type="text" id="login" name="login" placeholder={t("app.admin.dashboard.users.page.ka7b7cd19")} required />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="name" className="text-sm font-medium">{t("app.admin.dashboard.users.page.kb6f6dc96")}</label>
                            <Input type="text" id="name" name="name" placeholder={t("app.admin.dashboard.users.page.kbf45db26")} />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="password" className="text-sm font-medium">{t("app.admin.dashboard.users.page.kc84ff662")}</label>
                            <Input type="password" id="password" name="password" placeholder={t("app.admin.dashboard.users.page.kd9dcdbba")} required />
                        </div>
                        <div className="grid w-full max-w-sm items-center gap-1.5">
                            <label htmlFor="role" className="text-sm font-medium">{t("app.admin.dashboard.users.page.k0bd8550f")}</label>
                            <select
                                id="role"
                                name="role"
                                className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                <option value="USER">{t("app.admin.dashboard.users.page.k4dd15429")}</option>
                                <option value="ADMIN">{t("app.admin.dashboard.users.page.kf2e17f9d")}</option>
                            </select>
                        </div>
                        <div className="flex items-center gap-3">
                            <label className="flex items-center gap-2 text-sm text-muted-foreground">
                                <input type="checkbox" name="mustChangePassword" defaultChecked className="h-4 w-4 rounded border-input" />
                                {t("app.admin.dashboard.users.page.kc50b82ec")}
                            </label>
                            <Button type="submit">{t("app.admin.dashboard.users.page.k5239b728")}</Button>
                        </div>
                    </form>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>{t("app.admin.dashboard.users.page.k1dc90fa5")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>{t("app.admin.dashboard.users.page.kb6f6dc96")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.users.page.k27ba7ff8")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.users.page.k0bd8550f")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.users.page.k8aedcc01")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.users.page.kc52804de")}</TableHead>
                                <TableHead className="text-right">{t("app.admin.dashboard.users.page.kf6b236a0")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {users.map((user) => (
                                <TableRow key={user.id}>
                                    <TableCell className="font-medium">{user.name || "N/A"}</TableCell>
                                    <TableCell>{user.login}</TableCell>
                                    <TableCell>
                                        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${user.role === 'ADMIN' ? 'bg-purple-100 text-purple-800' : 'bg-gray-100 text-gray-800'}`}>
                                            {user.role === 'ADMIN' ? t("app.admin.dashboard.users.page.kf2e17f9d") : t("app.admin.dashboard.users.page.k4dd15429")}
                                        </span>
                                    </TableCell>
                                    <TableCell>{user.mustChangePassword ? t("app.admin.dashboard.users.page.ke1af8561") : t("app.admin.dashboard.users.page.kfe3f0ccf")}</TableCell>
                                    <TableCell>{user.createdAt ? new Date(user.createdAt).toLocaleDateString() : 'N/A'}</TableCell>
                                    <TableCell className="text-right flex justify-end items-center">
                                        <EditUserDialog user={user as { id: string; name: string | null; login: string; role: "ADMIN" | "USER"; mustChangePassword?: boolean }} />
                                        <form action={deleteUser.bind(null, user.id as string)}>
                                            <Button variant="destructive" size="sm">{t("app.admin.dashboard.users.page.k626f35dc")}</Button>
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
