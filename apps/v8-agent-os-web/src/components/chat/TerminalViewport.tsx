"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { isSurfaceVisible, useSurfaceVisible } from "@/hooks/use-surface-visible";
import "@xterm/xterm/css/xterm.css";
import { useT } from "@/components/providers/LocaleProvider";
import { readTerminalInstance } from "@/lib/terminal-instance";

type Lease = { terminal: Terminal; fit: FitAddon; element: HTMLDivElement; cursor: number; generation: string; running: boolean; touched: number };
const leases = new Map<string, Lease>();
function releaseOtherPrincipals(principal: string) {
    for (const [key, lease] of leases) if (!key.startsWith(`${principal}\n`)) { lease.terminal.dispose(); leases.delete(key); }
}
export function TerminalViewport({ path, kind, canInput = true, canTerminate = true, initialRunning = true, sensitive = false, onExit }: {
    path: string; kind: "manual" | "process"; canInput?: boolean; canTerminate?: boolean; initialRunning?: boolean; sensitive?: boolean; onExit?: () => void;
}) {
    const t = useT();
    const { data: session } = useSession();
    const visible = useSurfaceVisible();
    const principal = session?.user?.id || "";
    const host = useRef<HTMLDivElement>(null);
    const leaseRef = useRef<Lease | null>(null);
    const [state, setState] = useState("connecting");
    const [error, setError] = useState("");
    const [retry, setRetry] = useState(0);
    const [running, setRunning] = useState(initialRunning);
    const live = useRef({ canInput, sensitive, onExit, initialRunning });
    live.current = { canInput, sensitive, onExit, initialRunning };
    const inputQueue = useRef(Promise.resolve());
    const inputBytes = useRef(0);
    const epoch = useRef(0);
    const attached = useRef(false);
    const send = useCallback((text: string) => {
        if (!attached.current || !live.current.canInput || !leaseRef.current?.running) { setError("web.terminal.inputOffline"); return; }
        const bytes = new TextEncoder().encode(text).length;
        if (inputBytes.current + bytes > 256 * 1024) { setError("web.terminal.inputBusy"); return; }
        const generation = epoch.current;
        const target = kind === "process" && live.current.sensitive ? "sensitive-input" : "input";
        inputBytes.current += bytes;
        inputQueue.current = inputQueue.current.then(async () => {
            if (generation !== epoch.current || !attached.current) return;
            const response = await fetch(`${path}/${target}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(kind === "manual" ? { inputText: text } : { input_text: text, ...(target === "sensitive-input" ? { secret_type: "terminal_secret" } : {}) }) });
            if (!response.ok) throw new Error("web.terminal.inputFailed");
            if (generation === epoch.current) setError("");
        }).catch((reason) => { if (generation === epoch.current) { attached.current = false; setError(reason.message); setState("disconnected"); } }).finally(() => { inputBytes.current -= bytes; });
    }, [path, kind]);
    const paste = useCallback(async () => {
        const generation = epoch.current;
        const lease = leaseRef.current;
        if (!attached.current || !live.current.canInput || !lease?.running) { setError("web.terminal.inputOffline"); return; }
        try {
            if (!navigator.clipboard?.readText) throw new Error("clipboard_unavailable");
            const text = await navigator.clipboard.readText();
            if (generation !== epoch.current || leaseRef.current !== lease) return;
            lease.terminal.focus();
            // xterm owns bracketed-paste/newline semantics and emits the normal
            // onData path. Mutating its helper textarea does not send stdin.
            if (text) lease.terminal.paste(text);
        } catch { if (generation === epoch.current) setError("web.terminal.pasteFailed"); }
    }, []);
    useEffect(() => {
        releaseOtherPrincipals(principal);
        if (!principal || !host.current) return;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const controller = new AbortController();
        let observer: ResizeObserver | undefined;
        let resizeTimer: ReturnType<typeof setTimeout> | undefined;
        let data: { dispose(): void } | undefined;
        let attachedLease: Lease | undefined;
        const currentEpoch = ++epoch.current;
        attached.current = false;
        leaseRef.current = null;
        setState(visible ? "connecting" : "detached"); setError("");
        const initialize = async () => {
            const instanceId = await readTerminalInstance(controller.signal);
            if (cancelled || !host.current) return;
            const key = `${principal}\n${instanceId}\n${path}`;
            let lease = leases.get(key);
            if (lease?.element.isConnected) { setState("detached"); setError("web.terminal.openElsewhere"); return; }
            if (!lease) {
                const terminal = new Terminal({ allowTransparency: true, convertEol: false, cursorBlink: true, fontFamily: "Consolas, monospace", fontSize: 12, scrollback: 10000, theme: { background: "#05070b", foreground: "#e5e7eb" } });
                const fit = new FitAddon(); terminal.loadAddon(fit);
                const element = document.createElement("div"); element.className = "h-full w-full";
                host.current.append(element); terminal.open(element);
                lease = { terminal, fit, element, cursor: 0, generation: "", running: live.current.initialRunning, touched: Date.now() };
                leases.set(key, lease);
            } else host.current.append(lease.element);
            lease.touched = Date.now(); leaseRef.current = lease;
            attachedLease = lease;
            for (const [oldKey, old] of [...leases].sort((a,b) => a[1].touched - b[1].touched)) {
                if (leases.size <= 4) break;
                if (oldKey !== key && !old.element.isConnected) { old.terminal.dispose(); leases.delete(oldKey); }
            }
            let lastSize = "";
            const resize = () => {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(() => {
                    if (cancelled || !isSurfaceVisible() || !host.current?.clientWidth || !host.current.clientHeight) return;
                    lease.fit.fit();
                    const cols = Math.max(20, Math.min(240, lease.terminal.cols));
                    const rows = Math.max(4, Math.min(120, lease.terminal.rows));
                    const size = `${cols}:${rows}`;
                    if (size === lastSize) return;
                    lastSize = size;
                    void fetch(`${path}/resize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cols, rows }), signal: controller.signal }).catch(() => undefined);
                }, 80);
            };
            observer = new ResizeObserver(resize); observer.observe(host.current); resize();
            data = lease.terminal.onData(send);
            lease.terminal.attachCustomKeyEventHandler(event => {
                if (event.type !== "keydown" || !event.ctrlKey || event.altKey || event.metaKey) return true;
                const key = event.key.toLowerCase();
                if (key === "v" && event.shiftKey) {
                    event.preventDefault(); event.stopPropagation(); void paste(); return false;
                }
                if (key !== "c") return true;
                event.preventDefault(); event.stopPropagation();
                if (lease.terminal.hasSelection()) {
                    const selected = lease.terminal.getSelection();
                    void navigator.clipboard?.writeText(selected).catch(() => setError("web.terminal.copyFailed"));
                    lease.terminal.clearSelection();
                } else if (!event.shiftKey) {
                    send("\x03");
                }
                return false;
            });
            if (!visible) { setState("detached"); return; }
            let failures = 0;
            const poll = async () => {
                try {
                    const res = await fetch(`${path}?cursor=${lease.cursor}`, { cache: "no-store", signal: controller.signal });
                    if (cancelled || epoch.current !== currentEpoch) return;
                    if (res.status === 404) { lease.running = false; attached.current = false; setRunning(false); setState("exited"); return; }
                    if (!res.ok) throw new Error("web.terminal.connectionInterrupted");
                    const payload = await res.json();
                    if (cancelled) return;
                    if (payload.ok === false || payload.status === "not_found") { lease.running = false; attached.current = false; setRunning(false); setState("exited"); return; }
                    const generation = String(payload.outputGeneration || "");
                    if (payload.outputReset === true) { lease.terminal.reset(); lease.cursor = 0; lease.generation = generation; timer = setTimeout(poll, 0); return; }
                    if (generation && lease.generation && generation !== lease.generation) { lease.terminal.reset(); lease.cursor = 0; lease.generation = generation; timer = setTimeout(poll, 0); return; }
                    lease.generation = generation;
                    const text = String(kind === "manual" ? payload.outputDelta || "" : payload.output || "");
                    const nextCursor = Number(payload.outputCursor ?? lease.cursor);
                    // Exactly one bounded request and one write callback in flight.
                    if (text) await new Promise<void>(resolve => lease.terminal.write(text, resolve));
                    lease.cursor = nextCursor;
                    if (cancelled) return;
                    const isRunning = payload.isRunning ?? payload.is_running ?? payload.process?.is_running;
                    if (typeof isRunning === "boolean") lease.running = isRunning;
                    setRunning(lease.running); setState(lease.running ? "attached" : "exited"); setError(current => current === "web.terminal.connectionInterrupted" ? "" : current); attached.current = lease.running; failures = 0;
                    if (!lease.running && !payload.outputHasMore) { live.current.onExit?.(); return; }
                    timer = setTimeout(poll, text ? 0 : 250);
                } catch (reason) {
                    if (cancelled) return;
                    attached.current = false; setState("disconnected"); setError(reason instanceof Error ? reason.message : "web.terminal.connectionInterrupted");
                    if (++failures <= 5) timer = setTimeout(poll, Math.min(8000, 500 * 2 ** failures));
                }
            };
            void poll();
        };
        void initialize().catch(reason => { if (!cancelled) { setState("failed"); setError(reason.message); } });
        // epoch is a monotonic request token, not a DOM ref. Invalidate only our
        // own attachment so a stale cleanup cannot invalidate a replacement.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        return () => { cancelled = true; if (epoch.current === currentEpoch) { epoch.current++; attached.current = false; } controller.abort(); clearTimeout(timer); clearTimeout(resizeTimer); observer?.disconnect(); data?.dispose(); attachedLease?.terminal.attachCustomKeyEventHandler(() => true); attachedLease?.element.remove(); };
    }, [principal, path, kind, visible, retry, send, paste]);
    const terminate = async () => {
        const generation = epoch.current;
        try {
            const response = await fetch(`${path}/terminate`, { method: "POST" });
            const payload = await response.json();
            if (!response.ok || payload.ok === false) throw new Error("web.terminal.terminateFailed");
            if (generation === epoch.current) setRetry(value => value + 1);
        } catch (reason) { if (generation === epoch.current) setError(reason instanceof Error ? reason.message : "web.terminal.terminateFailed"); }
    };
    return <div className="flex h-full min-h-0 flex-col bg-[#05070b]">
        <div ref={host} className="min-h-0 flex-1 p-2" data-v8-context-menu-ignore onContextMenuCapture={event => { event.preventDefault(); event.stopPropagation(); void paste(); }} />
        <div className="flex items-center gap-2 border-t border-white/10 px-3 py-1 text-[11px] text-slate-300" role="status">
            <span>{t(`web.terminal.state.${state}`)}</span>
            {error ? <span className="truncate text-red-300" title={t(error)}>{t(error)}</span> : null}
            <button type="button" onClick={() => leaseRef.current?.terminal.scrollToBottom()} className="ml-auto whitespace-nowrap">{t("web.terminal.backToBottom")}</button>
            {["disconnected", "failed", "detached"].includes(state) ? <button type="button" onClick={() => setRetry(value => value + 1)}>{t("web.terminal.reconnect")}</button> : null}
            {running && canTerminate ? <button type="button" aria-label={t("web.terminal.terminate")} onClick={() => void terminate()}>{t("web.terminal.stop")}</button> : null}
        </div>
    </div>;
}
