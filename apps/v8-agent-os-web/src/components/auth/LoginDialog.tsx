"use client";

import { useEffect, useState } from "react";
import { signIn, useSession } from "next-auth/react";
import { Link2, Lock, LogIn, UserCircle2 } from "lucide-react";

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

type LoginMode = "pair" | "password";

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
    const [mode, setMode] = useState<LoginMode>("pair");
    const [pairingUri, setPairingUri] = useState("");
    const [login, setLogin] = useState("");
    const [password, setPassword] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const { data: session } = useSession();
    const t = useT();

    useEffect(() => {
        if (session) setOpen(false);
    }, [session]);

    function resetForm() {
        setPairingUri("");
        setLogin("");
        setPassword("");
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

    async function handlePasswordLogin() {
        setIsLoading(true);
        setError("");
        try {
            const result = await signIn("credentials", { login, password, redirect: false });
            if (!result?.ok || result.error) {
                throw new Error(t(lt("登录名或密码错误", "Incorrect login or password")));
            }
            resetForm();
            setOpen(false);
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : t(lt("登录失败", "Sign-in failed")));
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
                        {mode === "pair" ? t(lt("连接 V8 OS", "Connect to V8 OS")) : t(lt("Owner 登录", "Owner sign-in"))}
                    </DialogTitle>
                    <DialogDescription className="text-center text-white/60">
                        {mode === "pair"
                            ? t(lt("粘贴 Admin 生成的一次性链接，无需填写地址或创建账号。", "Paste the single-use link from Admin. No address or account creation is needed."))
                            : t(lt("仅在设备配对不可用时使用。", "Use this only when device pairing is unavailable."))}
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-5 py-5">
                    <div className="grid grid-cols-2 rounded-lg border border-white/10 bg-white/5 p-1">
                        <Button type="button" variant="ghost" onClick={() => setMode("pair")} className={mode === "pair" ? "bg-white text-black hover:bg-white/90" : "text-white/60"}>
                            {t(lt("设备配对", "Pair device"))}
                        </Button>
                        <Button type="button" variant="ghost" onClick={() => setMode("password")} className={mode === "password" ? "bg-white text-black hover:bg-white/90" : "text-white/60"}>
                            {t(lt("高级登录", "Advanced login"))}
                        </Button>
                    </div>

                    {error ? <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-center text-sm text-red-200">{error}</div> : null}

                    {mode === "pair" ? (
                        <div className="space-y-2">
                            <Label htmlFor="pairing-uri" className="text-white/80">{t(lt("配对链接", "Pairing link"))}</Label>
                            <div className="relative">
                                <Link2 className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                <Input id="pairing-uri" value={pairingUri} onChange={(event) => setPairingUri(event.target.value)} placeholder="v8agentosweb://pair?..." className="bg-white/5 pl-10 text-white placeholder:text-white/20" />
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="owner-login" className="text-white/80">{t(lt("Owner 登录名", "Owner login"))}</Label>
                                <div className="relative">
                                    <UserCircle2 className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                    <Input id="owner-login" value={login} onChange={(event) => setLogin(event.target.value)} className="bg-white/5 pl-10 text-white" />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="owner-password" className="text-white/80">{t(lt("密码", "Password"))}</Label>
                                <div className="relative">
                                    <Lock className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                    <Input id="owner-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="bg-white/5 pl-10 text-white" />
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                <DialogFooter>
                    <Button
                        onClick={() => void (mode === "pair" ? handlePairing() : handlePasswordLogin())}
                        disabled={isLoading || (mode === "pair" ? !pairingUri.trim() : !login.trim() || !password)}
                        className="w-full bg-white text-black hover:bg-white/90"
                    >
                        {isLoading ? t(lt("连接中...", "Connecting...")) : mode === "pair" ? t(lt("连接此设备", "Connect this device")) : t(lt("登录", "Sign in"))}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
