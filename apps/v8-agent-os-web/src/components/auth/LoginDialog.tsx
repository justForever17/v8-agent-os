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
import { LogIn, Lock, UserCircle2 } from "lucide-react";
import { useState, useEffect } from "react";
import { signIn, useSession } from "next-auth/react";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function LoginDialog() {
    const [open, setOpen] = useState(false);
    const [isRegistering, setIsRegistering] = useState(false);
    const [login, setLogin] = useState("");
    const [password, setPassword] = useState("");
    const [nickname, setNickname] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const { data: session } = useSession();
    const t = useT();

    // Auto-close dialog when logged in
    useEffect(() => {
        if (session) {
            setOpen(false);
        }
    }, [session]);


    const handleLogin = async () => {
        setIsLoading(true);
        setError("");
        try {
            const res = await signIn("credentials", {
                login,
                password,
                redirect: false,
            });

            if (res?.error) {
                throw new Error(t(lt("登录名或密码错误", "Incorrect login or password")));
            }

            if (res?.ok) {
                setOpen(false);
                resetForm();
                // Session will auto-update via SessionProvider
            }
        } catch (error) {
            setError(error instanceof Error ? error.message : "Login failed");
        } finally {
            setIsLoading(false);
        }
    };

    const handleRegister = async () => {
        console.log("Register button clicked");
        if (!login || !nickname || !password) {
            console.log("Missing fields", { login, nickname, password });
            setError(t(lt("请填写所有必填项", "Please fill in all required fields")));
            return;
        }

        setIsLoading(true);
        setError("");
        try {
            console.log("Sending register request...", { login, name: nickname });
            const res = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    login,
                    password,
                    name: nickname,
                }),
            });

            const data = await res.json();
            console.log("Register response:", data);

            if (!res.ok) {
                throw new Error(data.error || "Registration failed");
            }

            setIsRegistering(false);
            setOpen(false);
            alert(t(lt("注册成功！请登录。", "Registration successful. Please sign in.")));
        } catch (error) {
            console.error("Registration error", error);
            setError(error instanceof Error ? error.message : t(lt("注册失败，请重试", "Registration failed. Please retry.")));
        } finally {
            setIsLoading(false);
        }
    };

    const resetForm = () => {
        setLogin("");
        setPassword("");
        setNickname("");
        setError("");
    };

    return (
        <Dialog open={open} onOpenChange={(val) => {
            setOpen(val);
            if (!val) {
                resetForm();
                setIsRegistering(false);
            }
        }}>
            <DialogTrigger asChild>
                <Button variant="ghost" size="sm" className="hover:bg-white/10 transition-colors">
                    <LogIn className="w-4 h-4 mr-2" />
                    {t(lt("登录", "Sign in"))}
                </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[400px] bg-black/40 backdrop-blur-xl border-white/10 text-white shadow-2xl">
                <DialogHeader>
                    <DialogTitle className="text-2xl font-light tracking-wide text-center mb-2">
                        {isRegistering ? t(lt("加入我们", "Join V8 OS")) : t(lt("欢迎回来", "Welcome back"))}
                    </DialogTitle>
                        <DialogDescription className="text-center text-white/60">
                        {isRegistering
                            ? t(lt("先创建账号，再开始使用 V8 Agent OS", "Create an account to start using V8 Agent OS"))
                            : t(lt("登录后继续使用 V8 Agent OS", "Sign in to continue with V8 Agent OS"))}
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-6 py-6">
                    {error && (
                        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-200 text-sm text-center">
                            {error}
                        </div>
                    )}

                    {!isRegistering ? (
                        // LOGIN FORM
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="login" className="text-white/80">{t(lt("登录名", "Login"))}</Label>
                                <div className="relative">
                                    <UserCircle2 className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                    <Input
                                        id="login"
                                        value={login}
                                        onChange={(e) => setLogin(e.target.value)}
                                        placeholder={t(lt("输入登录名", "Enter your login"))}
                                        className="pl-10 bg-white/5 border-white/10 text-white placeholder:text-white/20 focus:border-primary/50 focus:ring-primary/20"
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="password" className="text-white/80">{t(lt("密码", "Password"))}</Label>
                                <div className="relative">
                                    <Lock className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                    <Input
                                        id="password"
                                        type="password"
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        placeholder="••••••"
                                        className="pl-10 bg-white/5 border-white/10 text-white placeholder:text-white/20 focus:border-primary/50 focus:ring-primary/20"
                                    />
                                </div>
                            </div>
                        </div>
                    ) : (
                        // REGISTER FLOW
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="reg-login" className="text-white/80">{t(lt("登录名", "Login"))}</Label>
                                <div className="relative">
                                    <UserCircle2 className="absolute left-3 top-3 h-4 w-4 text-white/40" />
                                    <Input
                                        id="reg-login"
                                        value={login}
                                        onChange={(e) => setLogin(e.target.value)}
                                        placeholder={t(lt("设置一个登录名", "Choose a login"))}
                                        className="pl-10 bg-white/5 border-white/10 text-white placeholder:text-white/20 focus:border-primary/50 focus:ring-primary/20"
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="nickname" className="text-white/80">{t(lt("昵称", "Display name"))}</Label>
                                <Input
                                    id="nickname"
                                    value={nickname}
                                    onChange={(e) => setNickname(e.target.value)}
                                    placeholder={t(lt("怎么称呼您？", "How should we call you?"))}
                                    className="bg-white/5 border-white/10 text-white placeholder:text-white/20"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="reg-password" className="text-white/80">{t(lt("设置密码", "Set password"))}</Label>
                                <Input
                                    id="reg-password"
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="••••••"
                                    className="bg-white/5 border-white/10 text-white placeholder:text-white/20"
                                />
                            </div>
                        </div>
                    )}
                </div>

                <DialogFooter className="flex-col sm:justify-between gap-4">
                    {!isRegistering ? (
                        <Button
                            onClick={handleLogin}
                            disabled={isLoading || !login || !password}
                            className="w-full bg-white text-black hover:bg-white/90 transition-all"
                        >
                            {isLoading ? t(lt("登录中...", "Signing in...")) : t(lt("登录", "Sign in"))}
                        </Button>
                    ) : (
                            <Button
                                onClick={handleRegister}
                                disabled={isLoading || !login || !nickname || !password}
                                className="w-full bg-white text-black hover:bg-white/90 transition-all"
                            >
                                {isLoading ? t(lt("注册中...", "Creating account...")) : t(lt("完成注册", "Create account"))}
                            </Button>
                    )}

                    <div className="flex justify-center w-full">
                        <Button
                            variant="link"
                            size="sm"
                            className="text-white/40 hover:text-white transition-colors"
                            onClick={() => {
                                const nextMode = !isRegistering;
                                resetForm();
                                setIsRegistering(nextMode);
                            }}
                        >
                            {isRegistering ? t(lt("已有账户？去登录", "Already have an account? Sign in")) : t(lt("没有账户？去注册", "No account? Create one"))}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
