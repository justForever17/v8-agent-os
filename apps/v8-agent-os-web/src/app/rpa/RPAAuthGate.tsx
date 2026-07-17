"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, LoaderCircle, RefreshCw } from "lucide-react";
import { signIn, useSession } from "next-auth/react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

export function RPAAuthGate() {
    const t = useT();
    const router = useRouter();
    const { status } = useSession();
    const attemptedRef = useRef(false);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);

    const connect = useCallback(async () => {
        if (busy) return;
        setBusy(true);
        setError("");
        try {
            const connectionResponse = await fetch("/api/connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ adminBaseUrl: "http://127.0.0.1:9528", persist: true }),
            });
            const connectionPayload = await connectionResponse.json().catch(() => ({}));
            if (!connectionResponse.ok) throw new Error(connectionPayload?.error || t("web.rpa.connectFailed"));
            const result = await signIn("credentials", {
                localSession: "1",
                adminBaseUrl: "http://127.0.0.1:9528",
                redirect: false,
            });
            if (result?.error) throw new Error(result.error);
            router.refresh();
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t("web.rpa.connectFailed"));
        } finally {
            setBusy(false);
        }
    }, [busy, router, t]);

    useEffect(() => {
        if (status === "unauthenticated" && !attemptedRef.current) {
            attemptedRef.current = true;
            void connect();
        }
        if (status === "authenticated") router.refresh();
    }, [connect, router, status]);

    return (
        <div className="flex h-full flex-col items-center justify-center px-6 py-10">
            <div className="w-full max-w-sm rounded-[2rem] border border-border/70 bg-background/90 p-7 text-center shadow-[0_28px_80px_-42px_rgba(15,23,42,0.45)] backdrop-blur-sm">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-7 w-7 text-primary" />
                </div>
                <div className="mt-5 space-y-2">
                    <h1 className="text-2xl font-semibold tracking-tight">{t("web.rpa.connectionTitle")}</h1>
                    <p className="text-sm leading-6 text-muted-foreground">{busy || status === "loading" ? t("web.rpa.connecting") : error || t("web.rpa.connectFailed")}</p>
                </div>
                <div className="mt-6 flex justify-center">
                    <Button type="button" onClick={() => void connect()} disabled={busy || status === "loading"} className="rounded-2xl">
                        {busy || status === "loading" ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {busy || status === "loading" ? t("web.rpa.connecting") : t("web.rpa.retryConnection")}
                    </Button>
                </div>
            </div>
        </div>
    );
}
