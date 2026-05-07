"use client";

import Link from "next/link";
import { Music2, Sparkles } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ti } from "@/i18n/admin-legacy";
export default function LegacyMusicPage() {
  const t = useT();
  return <div className="mx-auto max-w-3xl space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Legacy Background Music</h1>
                <p className="mt-2 text-muted-foreground">
                    {ti(t, "k90eee0fd15")}
                </p>
            </div>

            <Card className="border-violet-200 bg-violet-50/70">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Music2 className="h-5 w-5" />
                        {ti(t, "kccc6bb88d9")}
                    </CardTitle>
                    <CardDescription>
                        {ti(t, "kc9d11e2a35")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 text-sm text-slate-700">
                    <div className="rounded-md border bg-white p-3">{ti(t, "k0963515701")}</div>
                    <div className="rounded-md border bg-white p-3">{ti(t, "k0e58d09d1a")}</div>
                    <Button asChild>
                        <Link href="/admin/creative-media">
                            <Sparkles className="mr-2 h-4 w-4" />
                            {ti(t, "k7a21804bcc")}
                        </Link>
                    </Button>
                </CardContent>
            </Card>
        </div>;
}
