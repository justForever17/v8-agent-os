"use client";
import { useState } from "react";
import { ChevronDown, LockKeyhole, TerminalSquare } from "lucide-react";
import { isActiveCommandSessionStatus, type AdminProcessRef } from "@v8/session-realtime";
import { TerminalViewport } from "./TerminalViewport";
import { useT } from "@/components/providers/LocaleProvider";
export function InteractiveTerminalCard({ process, compact = false, onTerminated }: { process: AdminProcessRef; compact?: boolean; onTerminated?: (id: string) => void }) {
    const t = useT();
    const [collapsed, setCollapsed] = useState(compact);
    const [sensitive, setSensitive] = useState(false);
    const id = process.commandId || process.processId;
    return <div className="flex w-full flex-col overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-2 bg-muted px-3 py-2 text-xs">
            <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>
                <TerminalSquare className="h-4 w-4" /><span className="truncate">{process.title || process.commandPreview || id}</span><ChevronDown className="ml-auto h-4 w-4" />
            </button>
            {process.canInput ? <button type="button" aria-pressed={sensitive} title={t("web.terminal.sensitiveHint")} aria-label={t("web.terminal.sensitive")} onClick={() => setSensitive(value => !value)} className={sensitive ? "text-amber-600" : ""}><LockKeyhole className="h-4 w-4" /></button> : null}
        </div>
        {!collapsed ? <div className={compact ? "h-40" : "h-[280px]"}><TerminalViewport
            path={`/api/client/bg_processes/${encodeURIComponent(id)}`} kind="process" canInput={Boolean(process.canInput)}
            canTerminate={Boolean(process.canTerminate)} initialRunning={isActiveCommandSessionStatus(process.status)}
            sensitive={sensitive} onExit={() => onTerminated?.(process.processId)}
        /></div> : null}
    </div>;
}
