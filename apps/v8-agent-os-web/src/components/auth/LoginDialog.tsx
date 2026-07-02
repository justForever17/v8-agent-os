"use client";

import { useEffect, useState } from "react";
import { signIn, useSession } from "next-auth/react";
import { Link2, LogIn } from "lucide-react";

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
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

const DEFAULT_LOCAL_ADMIN_BASE_URL = "http://127.0.0.1:9528";

function parsePairingAdminBaseUrl(pairingUri: string) {
    const parsed = new URL(String(pairingUri || "").trim());
    const adminBaseUrl = String(parsed.searchParams.get("admin") || "").trim();
    const code = String(parsed.searchParams.get("code") || "").trim();
    if (!adminBaseUrl || !code) {
        throw new Error("invalid_pairing_link");
    }
    return adminBaseUrl;
}

export function LoginDialog() {
    const [open, setOpen] = useState(false);
    const [pairingUri, setPairingUri] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const { data: session } = useSession();
    const t = useT();

    useEffect(() => {
        if (session) setOpen(false);
    }, [session]);

    function resetForm() {
        setPairingUri("");
        setError("");
    }

    async function handlePairing() {
        if (!pairingUri.trim()) {
            setError(t(lt("请粘贴设备配对链接", "Paste a device pairing link")));
            return;
        }
        setIsLoading(true);
        setError("");
        try {
            const adminBaseUrl = parsePairingAdminBaseUrl(pairingUri);
            const connectionResponse = await fetch("/api/connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ adminBaseUrl, persist: true }),
            });
            if (!connectionResponse.ok) {
                const payload = await connectionResponse.json().catch(() => ({}));
                throw new Error(String(payload?.error || t(lt("无法连接 V8 OS 实例", "Cannot reach the V8 OS instance"))));
            }
            const result = await signIn("credentials", {
                pairingUri,
                redirect: false,
            });
            if (!result?.ok || result.error) {
                throw new Error(t(lt("配对链接无效、已过期或已使用", "The pairing link is invalid, expired, or already used")));
            }
            resetForm();
            setOpen(false);
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t(lt("设备配对失败", "Device pairing failed")));
        } finally {
            setIsLoading(false);
        }
    }

    async function handleLocalConnect() {
        setIsLoading(true);
        setError("");
        try {
            const connectionResponse = await fetch("/api/connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ adminBaseUrl: DEFAULT_LOCAL_ADMIN_BASE_URL, persist: true }),
            });
            if (!connectionResponse.ok) {
                const payload = await connectionResponse.json().catch(() => ({}));
                throw new Error(String(payload?.error || t(lt("无法连接本机 V8 OS 实例", "Cannot reach the local V8 OS instance"))));
            }
            const result = await signIn("credentials", {
                localSession: "1",
                adminBaseUrl: DEFAULT_LOCAL_ADMIN_BASE_URL,
                redirect: false,
            });
            if (!result?.ok || result.error) {
                throw new Error(t(lt("本机自动登录失败", "Local sign-in failed")));
            }
            resetForm();
            setOpen(false);
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t(lt("本机连接失败", "Local connection failed")));
        } finally {
            setIsLoading(false);
        }
    }

    return (
        <Dialog open={open} onOpenChange={(nextOpen) => {
            setOpen(nextOpen);
            if (!nextOpen) resetForm();
        }}>
            <DialogTrigger asChild>
                <Button variant="ghost" size="sm" className="transition-colors hover:bg-white/10">
                    <LogIn className="mr-2 h-4 w-4" />
                    {t(lt("连接", "Connect"))}
                </Button>
            </DialogTrigger>
            <DialogContent className="border-white/10 bg-black/50 text-white shadow-2xl backdrop-blur-xl sm:max-w-[420px]">
                <DialogHeader>
                    <DialogTitle className="text-center text-2xl font-light tracking-wide">
                        {t(lt("连接 V8 OS", "Connect to V8 OS"))}
                    </DialogTitle>
                    <DialogDescription className="text-center text-white/60">
                        {t(lt("本机桌面会自动连接；远程或备用场景可粘贴 Admin 的一次性配对链接。", "Local desktop connects automatically; paste an Admin pairing link for remote or fallback use."))}
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-5 py-5">
                    {error ? <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-center text-sm text-red-200">{error}</div> : null}

                    <div className="space-y-2">
                        <Label htmlFor="pairing-uri" className="text-white/80">{t(lt("配对链接", "Pairing link"))}</Label>
                        <div className="relative">
                            <Link2 className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                            <Input id="pairing-uri" value={pairingUri} onChange={(event) => setPairingUri(event.target.value)} placeholder="v8agentosweb://pair?..." className="bg-white/5 pl-10 text-white placeholder:text-white/20" />
                        </div>
                    </div>
                </div>

                <DialogFooter>
                    <div className="flex w-full flex-col gap-2">
                        <Button
                            onClick={() => void handleLocalConnect()}
                            disabled={isLoading}
                            className="w-full bg-white text-black hover:bg-white/90"
                        >
                            {isLoading ? t(lt("连接中...", "Connecting...")) : t(lt("连接本机 V8 OS", "Connect local V8 OS"))}
                        </Button>
                        <Button
                            onClick={() => void handlePairing()}
                            disabled={isLoading || !pairingUri.trim()}
                            variant="outline"
                            className="w-full border-white/20 bg-white/5 text-white hover:bg-white/10"
                        >
                            {t(lt("使用配对链接", "Use pairing link"))}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
