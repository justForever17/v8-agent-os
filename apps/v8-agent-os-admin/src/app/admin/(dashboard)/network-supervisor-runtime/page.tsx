import { cookies } from "next/headers";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { Card, CardContent } from "@/components/ui/card";
import { localizeAdminText } from "@/lib/admin-copy";
import { lt, parseLocale } from "@/lib/locale";

export default async function NetworkSupervisorRuntimePage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = (value: string | ReturnType<typeof lt>) => localizeAdminText(locale, value);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="NETWORK SUPERVISOR RUNTIME"
                description={lt(
                    "面向局域网与广域网的多 Supervisor 组网、定向唤醒与远端协作。",
                    "Multi-supervisor networking, directed wake, and remote collaboration across LAN and WAN.",
                )}
            />
            <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                <CardContent className="space-y-4 p-6">
                    <div className="text-sm font-semibold text-slate-900">{t(lt("开发中", "In development"))}</div>
                    <div className="text-sm leading-6 text-slate-500">
                        {t(lt(
                            "这个 runtime 不是普通消息通道，而是让一台 V8 主动发现、信任、唤醒并协作另一台 V8 的控制平面。",
                            "This runtime is not a simple transport. It is the control plane that lets one V8 discover, trust, wake, and collaborate with another V8.",
                        ))}
                    </div>
                    <div className="grid gap-3 md:grid-cols-3">
                        {[lt("局域网自动发现", "LAN discovery"), lt("广域网定向接入", "WAN bootstrap"), lt("定向唤醒与回执", "Directed wake and receipts")].map((label) => (
                            <div
                                key={String(label)}
                                className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium text-slate-900"
                            >
                                {t(label)}
                            </div>
                        ))}
                    </div>
                    <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-900">
                        {t(lt(
                            "首版方案已经收口为双栈：局域网自动发现 + 广域网 bootstrap 接入。节点身份使用 Ed25519，传输走 HTTPS/WSS，关键控制消息要求签名、时间戳与 nonce。",
                            "The first design is dual-stack: LAN discovery plus WAN bootstrap. Node identity uses Ed25519, transport uses HTTPS/WSS, and critical control messages require signatures, timestamps, and nonces.",
                        ))}
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">Repository docs</div>
                        <div className="mt-2 text-sm font-medium text-slate-900">
                            docs/
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                            E:\Projects\v8chat\v8-agent-os\docs
                        </div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-500">
                        {t(lt(
                            "当前页面先提供方案摘要。后续版本会补齐 peer 管理、网络健康、证书/信任状态和定向唤醒调试。",
                            "This page currently provides the implementation summary. Later versions will add peer management, network health, trust status, and directed wake diagnostics.",
                        ))}
                    </div>
                </CardContent>
            </Card>
        </AdminPageShell>
    );
}
