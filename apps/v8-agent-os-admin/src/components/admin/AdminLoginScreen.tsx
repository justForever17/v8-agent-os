"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { Loader2, Lock, UserCircle2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

type AdminLoginScreenProps = {
    bootstrapMode: boolean;
};

export function AdminLoginScreen({ bootstrapMode }: AdminLoginScreenProps) {
    const t = useT();
    const [login, setLogin] = useState("");
    const [name, setName] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [error, setError] = useState("");
    const [isLoading, setIsLoading] = useState(false);

    const handleLogin = async (event: React.FormEvent) => {
        event.preventDefault();
        setIsLoading(true);
        setError("");

        try {
            const result = await signIn("credentials", {
                login,
                password,
                redirect: false,
            });

            if (result?.error) {
                setError(t(lt("登录失败，请检查登录名和密码", "Sign-in failed. Check your login and password.")));
                return;
            }

            window.location.href = "/admin";
        } catch {
            setError(t(lt("发生错误，请稍后重试", "Something went wrong. Please try again.")));
        } finally {
            setIsLoading(false);
        }
    };

    const handleBootstrap = async (event: React.FormEvent) => {
        event.preventDefault();
        if (password !== confirmPassword) {
            setError(t(lt("两次输入的密码不一致", "The passwords do not match.")));
            return;
        }

        setIsLoading(true);
        setError("");
        try {
            const response = await fetch("/api/auth/bootstrap", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ login, name, password }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(data.error || t(lt("首次设置失败", "Initial setup failed."))));
            }

            const result = await signIn("credentials", {
                login,
                password,
                redirect: false,
            });
            if (result?.error) {
                throw new Error(t(lt("管理员创建成功，但自动登录失败", "The admin account was created, but automatic sign-in failed.")));
            }

            window.location.href = "/admin";
        } catch (err) {
            setError(err instanceof Error ? err.message : t(lt("首次设置失败", "Initial setup failed.")));
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-muted/20 px-4 py-12">
            <Card className="w-full max-w-md shadow-lg border-0 sm:border">
                <CardHeader className="space-y-2 text-center">
                    <CardTitle className="text-2xl font-bold tracking-tight">
                        {bootstrapMode
                            ? t(lt("首次设置管理台", "Set up admin console"))
                            : t(lt("管理员登录", "Admin sign in"))}
                    </CardTitle>
                    <CardDescription>
                        {bootstrapMode
                            ? t(lt("先设置管理员登录名和密码，再进入配置页。", "Create the admin login first, then enter the console."))
                            : t(lt("请输入管理员登录名和密码。", "Enter your admin login and password."))}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={bootstrapMode ? handleBootstrap : handleLogin} className="space-y-4">
                        {error ? (
                            <Alert variant="destructive">
                                <AlertDescription>{error}</AlertDescription>
                            </Alert>
                        ) : null}
                        <div className="space-y-2">
                            <Label htmlFor="login">{t(lt("登录名", "Login"))}</Label>
                            <div className="relative">
                                <UserCircle2 className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                                <Input
                                    id="login"
                                    type="text"
                                    autoComplete="username"
                                    placeholder={bootstrapMode
                                        ? t(lt("例如：admin", "Example: admin"))
                                        : t(lt("输入登录名", "Enter your login"))}
                                    className="pl-9"
                                    value={login}
                                    onChange={(event) => setLogin(event.target.value)}
                                    required
                                    disabled={isLoading}
                                />
                            </div>
                        </div>
                        {bootstrapMode ? (
                            <div className="space-y-2">
                                <Label htmlFor="name">{t(lt("昵称", "Display name"))}</Label>
                                <Input
                                    id="name"
                                    type="text"
                                    autoComplete="name"
                                    placeholder={t(lt("例如：管理员", "Example: Admin"))}
                                    value={name}
                                    onChange={(event) => setName(event.target.value)}
                                    required
                                    disabled={isLoading}
                                />
                            </div>
                        ) : null}
                        <div className="space-y-2">
                            <Label htmlFor="password">
                                {bootstrapMode ? t(lt("设置密码", "Set password")) : t(lt("密码", "Password"))}
                            </Label>
                            <div className="relative">
                                <Lock className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                                <Input
                                    id="password"
                                    type="password"
                                    autoComplete={bootstrapMode ? "new-password" : "current-password"}
                                    className="pl-9"
                                    value={password}
                                    onChange={(event) => setPassword(event.target.value)}
                                    required
                                    disabled={isLoading}
                                />
                            </div>
                        </div>
                        {bootstrapMode ? (
                            <div className="space-y-2">
                                <Label htmlFor="confirmPassword">{t(lt("确认密码", "Confirm password"))}</Label>
                                <Input
                                    id="confirmPassword"
                                    type="password"
                                    autoComplete="new-password"
                                    value={confirmPassword}
                                    onChange={(event) => setConfirmPassword(event.target.value)}
                                    required
                                    disabled={isLoading}
                                />
                            </div>
                        ) : null}
                        <Button type="submit" className="w-full" disabled={isLoading}>
                            {isLoading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    {bootstrapMode ? t(lt("设置中...", "Setting up...")) : t(lt("登录中...", "Signing in..."))}
                                </>
                            ) : bootstrapMode ? t(lt("完成首次设置", "Finish setup")) : t(lt("登录", "Sign in"))}
                        </Button>
                    </form>
                </CardContent>
                <CardFooter className="justify-center">
                    <p className="text-xs text-muted-foreground">
                        {bootstrapMode
                            ? t(lt("完成后会自动进入管理台。", "You'll enter the admin console automatically after setup."))
                            : t(lt("仅限授权人员访问。", "Authorized access only."))}
                    </p>
                </CardFooter>
            </Card>
        </div>
    );
}
