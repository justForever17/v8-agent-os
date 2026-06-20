import { getUsers } from "@/lib/actions/users";
import { cookies } from "next/headers";
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
import { DevicePairingPanel } from "@/components/admin/DevicePairingPanel";
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
                    <CardTitle>{t("app.admin.dashboard.users.page.ownerAndDevices")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <DevicePairingPanel />
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
                                        {user.role === "ADMIN" ? (
                                            <EditUserDialog user={user as { id: string; name: string | null; login: string; role: "ADMIN" | "USER"; mustChangePassword?: boolean }} />
                                        ) : null}
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
