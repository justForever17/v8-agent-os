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
                setError(t("components.admin.AdminLoginScreen.k4a5737fe"));
                return;
            }

            window.location.href = "/admin";
        } catch {
            setError(t("components.admin.AdminLoginScreen.k8d313aaf"));
        } finally {
            setIsLoading(false);
        }
    };

    const handleBootstrap = async (event: React.FormEvent) => {
        event.preventDefault();
        if (password !== confirmPassword) {
            setError(t("components.admin.AdminLoginScreen.kc494ae80"));
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
                throw new Error(String(data.error || t("components.admin.AdminLoginScreen.k3407cd6e")));
            }

            const result = await signIn("credentials", {
                login,
                password,
                redirect: false,
            });
            if (result?.error) {
                throw new Error(t("components.admin.AdminLoginScreen.k1b4fc451"));
            }

            window.location.href = "/admin";
        } catch (err) {
            setError(err instanceof Error ? err.message : t("components.admin.AdminLoginScreen.k3407cd6e"));
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
                            ? t("components.admin.AdminLoginScreen.k15bbd23b")
                            : t("components.admin.AdminLoginScreen.k7fb1b1d9")}
                    </CardTitle>
                    <CardDescription>
                        {bootstrapMode
                            ? t("components.admin.AdminLoginScreen.k9da1e463")
                            : t("components.admin.AdminLoginScreen.ke6914745")}
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
                            <Label htmlFor="login">{t("components.admin.AdminLoginScreen.k27ba7ff8")}</Label>
                            <div className="relative">
                                <UserCircle2 className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                                <Input
                                    id="login"
                                    type="text"
                                    autoComplete="username"
                                    placeholder={bootstrapMode
                                        ? t("components.admin.AdminLoginScreen.ka7b7cd19")
                                        : t("components.admin.AdminLoginScreen.k2f2f47d8")}
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
                                <Label htmlFor="name">{t("components.admin.AdminLoginScreen.k59737457")}</Label>
                                <Input
                                    id="name"
                                    type="text"
                                    autoComplete="name"
                                    placeholder={t("components.admin.AdminLoginScreen.kbf45db26")}
                                    value={name}
                                    onChange={(event) => setName(event.target.value)}
                                    required
                                    disabled={isLoading}
                                />
                            </div>
                        ) : null}
                        <div className="space-y-2">
                            <Label htmlFor="password">
                                {bootstrapMode ? t("components.admin.AdminLoginScreen.kc88150fa") : t("components.admin.AdminLoginScreen.ka3779233")}
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
                                <Label htmlFor="confirmPassword">{t("components.admin.AdminLoginScreen.k641b208a")}</Label>
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
                                    {bootstrapMode ? t("components.admin.AdminLoginScreen.kdc92690a") : t("components.admin.AdminLoginScreen.k057cb3cb")}
                                </>
                            ) : bootstrapMode ? t("components.admin.AdminLoginScreen.kea4c62a1") : t("components.admin.AdminLoginScreen.k97aefe66")}
                        </Button>
                    </form>
                </CardContent>
                <CardFooter className="justify-center">
                    <p className="text-xs text-muted-foreground">
                        {bootstrapMode
                            ? t("components.admin.AdminLoginScreen.k5fe1be7e")
                            : t("components.admin.AdminLoginScreen.kcf04987c")}
                    </p>
                </CardFooter>
            </Card>
        </div>
    );
}
