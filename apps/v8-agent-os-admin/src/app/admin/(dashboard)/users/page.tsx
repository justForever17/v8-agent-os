import { cookies } from "next/headers";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { DevicePairingPanel } from "@/components/admin/DevicePairingPanel";
import { createTranslator, parseLocale } from "@/lib/locale";
import { listUsers } from "@/lib/users";

export default async function UsersPage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = createTranslator(locale);
    const owner = listUsers().find((user) => user.role === "ADMIN") || null;

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

            {owner ? (
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.users.page.ownerAccount")}</CardTitle>
                    </CardHeader>
                    <CardContent className="grid gap-3 text-sm text-slate-700 sm:grid-cols-3">
                        <div>
                            <div className="text-xs text-slate-500">{t("app.admin.dashboard.users.page.kb6f6dc96")}</div>
                            <div className="mt-1 font-medium">{owner.name || "-"}</div>
                        </div>
                        <div>
                            <div className="text-xs text-slate-500">{t("app.admin.dashboard.users.page.k27ba7ff8")}</div>
                            <div className="mt-1 font-mono">{owner.login}</div>
                        </div>
                        <div>
                            <div className="text-xs text-slate-500">{t("app.admin.dashboard.users.page.kc52804de")}</div>
                            <div className="mt-1">{owner.createdAt ? new Date(owner.createdAt).toLocaleDateString() : "-"}</div>
                        </div>
                    </CardContent>
                </Card>
            ) : null}
        </div>
    );
}
