"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function ConnectErrorClient() {
    const t = useT();

    return (
        <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 dark:bg-slate-950">
            <div className="w-full max-w-lg rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                    {t(lt("连接已失效", "Connection lost"))}
                </h1>
                <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-400">
                    {t(lt(
                        "当前 Web 没有拿到可用的管理台连接，或者原来的连接地址已经不可达。请重新建立连接后再继续登录和使用。",
                        "This Web app does not have a usable admin console connection, or the previous endpoint is no longer reachable. Reconnect before signing in or continuing."
                    ))}
                </p>
                <div className="mt-6 flex justify-center gap-3">
                    <Link href="/connect">
                        <Button>{t(lt("重新连接管理台", "Reconnect"))}</Button>
                    </Link>
                    <Link href="/">
                        <Button variant="outline">{t(lt("返回首页", "Back home"))}</Button>
                    </Link>
                </div>
            </div>
        </div>
    );
}
