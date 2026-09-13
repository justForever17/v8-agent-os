"use client";
import * as React from "react";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";

type NodeAddress = { advertisedBaseUrl: string; advertisedWsUrl: string; peerBaseUrl: string };
type Setup = { suggestions: { origin: string; kind: string }[]; warning?: string; discoveryError?: string };
export function NetworkTransportSetup({ node, onChange }: {
    node: NodeAddress; onChange: (patch: Partial<NodeAddress>) => void;
}) {
    const t = useT();
    const [setup, setSetup] = React.useState<Setup | null>(null);
    const [notice, setNotice] = React.useState("");
    const [probing, setProbing] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    React.useEffect(() => {
        const controller = new AbortController();
        fetch("/api/network-supervisor/neighbors/setup", { signal: controller.signal, cache: "no-store" })
            .then(async response => { if (!response.ok) throw Error(); return response.json(); })
            .then(setSetup).catch(() => { if (!controller.signal.aborted) setNotice("networkTransport.loadFailed"); });
        return () => controller.abort();
    }, []);
    const origin = node.peerBaseUrl || node.advertisedBaseUrl;
    const setOrigin = (value: string) => {
        onChange({ advertisedBaseUrl: value, peerBaseUrl: value, advertisedWsUrl: "" });
        setNotice("");
    };
    const probe = async () => {
        setProbing(true); setNotice("");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/setup/probe", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ origin }),
            });
            const result = await response.json();
            setNotice(response.ok && result.routeReachable ? "networkTransport.routeReady" : "networkTransport.routeFailed");
        } catch { setNotice("networkTransport.routeFailed"); }
        finally { setProbing(false); }
    };
    const save = async () => {
        setSaving(true); setNotice("");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/setup", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ origin, wsUrl: node.advertisedWsUrl }),
            });
            const result = await response.json();
            if (!response.ok) throw Error();
            onChange({ advertisedBaseUrl: result.origin, peerBaseUrl: result.origin, advertisedWsUrl: result.wsUrl });
            setNotice("networkTransport.saved");
        } catch { setNotice("networkTransport.saveFailed"); }
        finally { setSaving(false); }
    };
    return <ConfigCard title="networkTransport.title" description="networkTransport.description" variant="editor" bodyHeight="auto">
        <div className="space-y-3">
            <Label htmlFor="network-peer-origin">{t("networkTransport.address")}</Label>
            <div className="flex flex-wrap gap-2">
                <Input id="network-peer-origin" className="min-w-56 flex-1" value={origin} onChange={event => setOrigin(event.target.value)} placeholder="https://v8.example.com" />
                <Button variant="outline" disabled={probing || !origin} onClick={() => void probe()}>{t(probing ? "networkTransport.checking" : "networkTransport.check")}</Button>
                <Button disabled={saving} onClick={() => void save()}>{t(saving ? "networkTransport.saving" : "networkTransport.save")}</Button>
            </div>
            {setup?.suggestions?.length ? <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">{t("networkTransport.suggestions")}</span>
                {setup.suggestions.map(item => <Button size="sm" variant="outline" key={`${item.kind}:${item.origin}`} onClick={() => setOrigin(item.origin)}>{item.kind === "tailscale" ? "Tailscale" : "LAN"} · {item.origin}</Button>)}
            </div> : null}
            <p className="text-xs text-muted-foreground">{t("networkTransport.hint")}</p>
            {setup?.warning && origin === node.advertisedBaseUrl && /localhost|127\.|\[::1\]/.test(origin) ? <p className="text-sm text-amber-700 dark:text-amber-300">{t("networkTransport.loopback")}</p> : null}
            {setup?.discoveryError ? <p className="text-sm text-amber-700 dark:text-amber-300">{t("networkTransport.discoveryUnavailable")}</p> : null}
            {notice ? <p role="status" className="text-sm">{t(notice)}</p> : null}
            <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">{t("networkTransport.advanced")}</summary>
                <div className="mt-3 space-y-2"><Label htmlFor="network-peer-ws">{t("networkTransport.ws")}</Label>
                    <Input id="network-peer-ws" value={node.advertisedWsUrl} onChange={event => onChange({ advertisedWsUrl: event.target.value })} placeholder="wss://engine.example.com/v1/network-supervisor/peer/ws" />
                    <p className="text-xs text-muted-foreground">{t("networkTransport.wsHint")}</p>
                </div>
            </details>
        </div>
    </ConfigCard>;
}
