'use client';

import React, { useMemo } from 'react';
import { Plus, TerminalSquare, X } from 'lucide-react';
import { TerminalViewport } from './TerminalViewport';

import { isActiveCommandSessionStatus, type AdminProcessRef } from '@v8/session-realtime';
import { useT } from '@/components/providers/LocaleProvider';
import { cn } from '@/lib/utils';
import { InteractiveTerminalCard } from './InteractiveTerminalCard';
import '@xterm/xterm/css/xterm.css';

export interface TerminalProfileView {
    id: string;
    label: string;
    command?: string;
    executable?: string;
}

export interface ManualTerminalSessionView {
    conversationId?: string;
    workspaceId?: string;
    ok?: boolean;
    sessionId?: string;
    commandId?: string;
    profileId?: string;
    profileLabel?: string;
    cwd?: string;
    status?: string;
    outputDelta?: string;
    screenSnapshot?: string;
    rawScreenSnapshot?: string;
    isRunning?: boolean;
    awaitingInput?: boolean;
    usesTty?: boolean;
    returnCode?: number | string | null;
    error?: string;
    detail?: string;
}

interface ManualTerminalPanelProps {
    workspacePath?: string;
    profiles: TerminalProfileView[];
    profileId: string;
    sessions: ManualTerminalSessionView[];
    processes?: AdminProcessRef[];
    activeTabId: string;
    hiddenTabCount?: number;
    busy?: boolean;
    error?: string;
    onProfileChange: (profileId: string) => void;
    onStart: () => void;
    onActivate: (tabId: string) => void;
    onHideTab: (tabId: string) => Promise<void> | void;
    onShowHidden?: () => void;
    onClosePanel: () => void;
}

function formatTerminalTitle(session: ManualTerminalSessionView) {
    const label = session.profileLabel || session.profileId || 'Terminal';
    const id = String(session.sessionId || session.commandId || '').trim();
    const suffix = id ? ` · ${id.slice(-6)}` : '';
    return `${label}${suffix}`;
}

function isProcessRunning(process: AdminProcessRef) {
    return isActiveCommandSessionStatus(process.status);
}

function formatProcessTitle(process: AdminProcessRef) {
    const id = String(process.commandId || process.processId || '').trim();
    const shortId = id ? ` · ${id.slice(-6)}` : '';
    return `${process.title || process.commandPreview || 'Process'}${shortId}`;
}

export function ManualTerminalPanel({
    workspacePath,
    profiles,
    profileId,
    sessions,
    processes = [],
    activeTabId,
    hiddenTabCount = 0,
    busy,
    error,
    onProfileChange,
    onStart,
    onActivate,
    onHideTab,
    onShowHidden,
    onClosePanel,
}: ManualTerminalPanelProps) {
    const t = useT();
    const tabs = useMemo(() => {
        const manualCommandIds = new Set(
            sessions
                .map((session) => String(session.commandId || session.sessionId || '').trim())
                .filter(Boolean),
        );
        return [
            ...sessions
                .map((session) => {
                    const sessionId = String(session.sessionId || '').trim();
                    if (!sessionId) {
                        return null;
                    }
                    return {
                        id: `manual:${sessionId}`,
                        kind: 'manual' as const,
                        title: formatTerminalTitle(session),
                        isRunning: session.isRunning !== false,
                        session,
                    };
                })
                .filter((item): item is NonNullable<typeof item> => Boolean(item)),
            ...processes
                .map((process) => {
                    const processId = String(process.processId || process.commandId || '').trim();
                    if (!processId || manualCommandIds.has(processId) || manualCommandIds.has(String(process.commandId || '').trim())) {
                        return null;
                    }
                    return {
                        id: `process:${processId}`,
                        kind: 'process' as const,
                        title: formatProcessTitle(process),
                        isRunning: isProcessRunning(process),
                        process,
                    };
                })
                .filter((item): item is NonNullable<typeof item> => Boolean(item)),
        ];
    }, [processes, sessions]);

    const activeTab = useMemo(
        () => tabs.find((item) => item.id === activeTabId) || tabs[0] || null,
        [activeTabId, tabs],
    );

    return (
        <div className="z-30 flex h-72 shrink-0 flex-col overflow-hidden border-t border-border/60 bg-background/95 shadow-sm backdrop-blur sm:max-h-[36vh]">
            <div className="flex min-h-10 items-center gap-2 border-b border-border/50 bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
                <div className="flex min-w-0 items-center gap-2">
                    <TerminalSquare className="h-3.5 w-3.5 shrink-0" />
                    <span className="font-semibold text-foreground">{t('web.terminal.title')}</span>
                    <span title={t('web.terminal.locationHint', { workspace: workspacePath || t('web.terminal.unboundWorkspace'), cwd: activeTab?.kind === "manual" ? activeTab.session.cwd || t('web.terminal.unknownDirectory') : t('web.terminal.agentCommand') })} className="max-w-[28vw] truncate font-mono text-muted-foreground/80">
                        {activeTab?.kind === 'manual' ? activeTab.session.cwd || workspacePath : workspacePath || t('web.terminal.unboundWorkspace')}
                    </span>
                </div>
                <div className="ml-2 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                    {tabs.map((tab) => {
                        const active = activeTab?.id === tab.id;
                        return (
                            <div
                                key={tab.id}
                                role="button"
                                tabIndex={0}
                                className={cn(
                                    "group flex max-w-[180px] shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-left transition-colors",
                                    active
                                        ? "border-primary/45 bg-primary/10 text-foreground"
                                        : "border-border/50 bg-background/80 hover:bg-muted",
                                )}
                                onClick={() => onActivate(tab.id)}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        onActivate(tab.id);
                                    }
                                }}
                                title={tab.title}
                            >
                                <span className={cn("h-1.5 w-1.5 rounded-full", tab.isRunning ? "bg-emerald-500" : "bg-slate-400")} />
                                <span className="truncate font-mono">{tab.title}</span>
                                <span
                                    role="button"
                                    tabIndex={0}
                                    className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded opacity-55 hover:bg-muted-foreground/10 hover:opacity-100"
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        void onHideTab(tab.id);
                                    }}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter' || event.key === ' ') {
                                            event.stopPropagation();
                                            void onHideTab(tab.id);
                                        }
                                    }}
                                    title={t('web.terminal.hideTab')}
                                >
                                    <X className="h-3 w-3" />
                                </span>
                            </div>
                        );
                    })}
                </div>
                {hiddenTabCount > 0 && onShowHidden && (
                    <button
                        type="button"
                        className="inline-flex h-7 shrink-0 items-center rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                        onClick={onShowHidden}
                        title={t('web.terminal.showHidden')}
                    >
                        {t('web.terminal.showHiddenCount', { count: hiddenTabCount })}
                    </button>
                )}
                {profiles.length > 1 && (
                    <select
                        value={profileId}
                        onChange={(event) => onProfileChange(event.target.value)}
                        className="h-7 max-w-44 shrink-0 rounded-md border border-border/50 bg-background px-2 text-[11px] text-foreground outline-none"
                    >
                        {profiles.map((profile) => (
                            <option key={profile.id} value={profile.id}>
                                {profile.label}
                            </option>
                        ))}
                    </select>
                )}
                <button
                    type="button"
                    className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                    onClick={onStart}
                    disabled={busy || !profiles.length}
                    title={t('web.terminal.newTerminal')}
                >
                    <Plus className="h-3.5 w-3.5" />
                    {t('web.terminal.new')}
                </button>
                <button
                    type="button"
                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={onClosePanel}
                    title={t('web.terminal.collapse')}
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            </div>
            {error && activeTab ? <div role="alert" className="bg-[#05070b] px-3 py-1 text-[11px] text-red-300">{error}</div> : null}
            {activeTab?.kind === 'manual' ? (
                <TerminalViewport key={activeTab.session.sessionId} path={`/api/client/terminal/sessions/${encodeURIComponent(activeTab.session.sessionId || "")}`} kind="manual" initialRunning={activeTab.session.isRunning !== false} canInput={activeTab.session.usesTty !== false} />
            ) : activeTab?.kind === 'process' ? (
                <div className="min-h-0 flex-1 overflow-auto bg-[#05070b] p-2">
                    <InteractiveTerminalCard
                        key={activeTab.process.processId}
                        process={activeTab.process}
                    />
                </div>
            ) : (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-[#05070b] text-[12px] text-slate-400">
                    <TerminalSquare className="h-8 w-8 opacity-45" />
                    <button
                        type="button"
                        className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-slate-200 hover:bg-white/10 disabled:opacity-50"
                        onClick={onStart}
                        disabled={busy || !profiles.length}
                    >
                        {t('web.terminal.createWorkspaceTerminal')}
                    </button>
                    {error && <div className="max-w-md text-center text-red-300">{error}</div>}
                </div>
            )}
        </div>
    );
}
