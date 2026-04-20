import { Mail } from "lucide-react";
import { cookies, headers } from "next/headers";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { createTranslator, LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";

export default async function VerifyRequest() {
    const cookieStore = await cookies();
    const headerStore = await headers();
    const locale = resolveInitialLocale(
        cookieStore.get(LOCALE_COOKIE_NAME)?.value,
        headerStore.get("accept-language"),
    );
    const t = createTranslator(locale);

    return (
        <div className="flex min-h-screen items-center justify-center bg-muted/10">
            <Card className="w-full max-w-md text-center">
                <CardHeader>
                    <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                        <Mail className="h-6 w-6 text-primary" />
                    </div>
                    <CardTitle>{t("app.admin.verify.title")}</CardTitle>
                    <CardDescription>
                        {t("app.admin.verify.descriptionLine1")}
                        <br />
                        {t("app.admin.verify.descriptionLine2")}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">{t("app.admin.verify.hint")}</p>
                </CardContent>
            </Card>
        </div>
    );
}
