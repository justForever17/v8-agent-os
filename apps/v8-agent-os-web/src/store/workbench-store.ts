"use client";

import { create } from "zustand";

import {
    createSessionOverviewDocument,
    clearWorkbenchDocumentPayload,
    normalizeWorkbenchDocument,
    type WorkbenchDocument,
    type WorkbenchMode,
    type WorkbenchTab,
} from "@/lib/workbench";

const STORAGE_VERSION = 2;
const STORAGE_PREFIX = "v8-web-workbench";
const MIN_STORED_WIDTH = 200;
const MAX_STORED_WIDTH = 960;
const MAX_TABS = 12;

function defaultWidth() {
    // Zero means "use the Workbench container's 10:5 default ratio". Once the
    // user drags the divider, an absolute per-session width is persisted.
    return 0;
}

type OpenDocumentOptions = {
    activate?: boolean;
    mode?: Exclude<WorkbenchMode, "closed">;
    markUnread?: boolean;
};

type PersistedWorkbenchState = {
    version: number;
    mode: WorkbenchMode;
    width: number;
    tabs: WorkbenchTab[];
    activeDocumentId: string | null;
};

interface WorkbenchStoreState {
    sessionId: string | null;
    boundAt: number;
    hydrated: boolean;
    mode: WorkbenchMode;
    width: number;
    tabs: WorkbenchTab[];
    activeDocumentId: string | null;
    runtimeFocusSuppressed: boolean;
    bindSession: (sessionId: string | null) => void;
    ensureOverview: () => void;
    openDocument: (document: WorkbenchDocument, options?: OpenDocumentOptions) => void;
    activateDocument: (documentId: string) => void;
    updateDocument: (document: WorkbenchDocument, options?: Pick<OpenDocumentOptions, "activate" | "markUnread">) => void;
    markDocumentUnavailable: (documentId: string, reason?: string) => void;
    closeDocument: (documentId: string) => void;
    setMode: (mode: WorkbenchMode) => void;
    toggle: () => void;
    setWidth: (width: number) => void;
}

function clampStoredWidth(value: number) {
    if (!Number.isFinite(value) || value <= 0) return defaultWidth();
    return Math.min(MAX_STORED_WIDTH, Math.max(MIN_STORED_WIDTH, Math.round(value)));
}

function storageKey(sessionId: string) {
    return `${STORAGE_PREFIX}:${sessionId}`;
}

function canUseStorage() {
    return typeof window !== "undefined" && Boolean(window.localStorage);
}

function normalizePersistedTab(value: unknown): WorkbenchTab | null {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    const document = normalizeWorkbenchDocument(record.document);
    if (!document || document.lifecycle === "runtime") return null;
    const openedAt = Number(record.openedAt);
    const lastActivatedAt = Number(record.lastActivatedAt);
    return {
        document,
        unread: Boolean(record.unread),
        openedAt: Number.isFinite(openedAt) ? openedAt : Date.now(),
        lastActivatedAt: Number.isFinite(lastActivatedAt) ? lastActivatedAt : 0,
    };
}

function readPersistedState(sessionId: string): PersistedWorkbenchState | null {
    if (!canUseStorage()) return null;
    try {
        const raw = window.localStorage.getItem(storageKey(sessionId));
        if (!raw) return null;
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        if (Number(parsed.version) !== STORAGE_VERSION) return null;
        const tabs = Array.isArray(parsed.tabs)
            ? parsed.tabs.map(normalizePersistedTab).filter((tab): tab is WorkbenchTab => Boolean(tab)).slice(-MAX_TABS)
            : [];
        const requestedMode = String(parsed.mode || "closed") as WorkbenchMode;
        const mode: WorkbenchMode = ["closed", "split", "focus"].includes(requestedMode) ? requestedMode : "closed";
        const requestedActiveId = typeof parsed.activeDocumentId === "string" ? parsed.activeDocumentId : null;
        const activeDocumentId = tabs.some((tab) => tab.document.documentId === requestedActiveId)
            ? requestedActiveId
            : tabs.at(-1)?.document.documentId || null;
        return {
            version: STORAGE_VERSION,
            mode: tabs.length > 0 ? mode : "closed",
            width: clampStoredWidth(Number(parsed.width)),
            tabs,
            activeDocumentId,
        };
    } catch {
        return null;
    }
}

function persistState(state: Pick<WorkbenchStoreState, "sessionId" | "mode" | "width" | "tabs" | "activeDocumentId">) {
    if (!state.sessionId || !canUseStorage()) return;
    const payload: PersistedWorkbenchState = {
        version: STORAGE_VERSION,
        mode: state.mode,
        width: clampStoredWidth(state.width),
        tabs: state.tabs.filter((tab) => tab.document.lifecycle === "session").slice(-MAX_TABS),
        activeDocumentId: state.activeDocumentId,
    };
    try {
        window.localStorage.setItem(storageKey(state.sessionId), JSON.stringify(payload));
    } catch {
        // Local recovery is best effort; an unavailable storage quota must not block chat.
    }
}

function trimTabs(tabs: WorkbenchTab[], protectedId: string) {
    if (tabs.length <= MAX_TABS) return tabs;
    const removable = tabs
        .filter((tab) => tab.document.documentId !== protectedId && tab.document.kind !== "session_overview")
        .sort((left, right) => left.lastActivatedAt - right.lastActivatedAt);
    const removeIds = new Set(removable.slice(0, tabs.length - MAX_TABS).map((tab) => tab.document.documentId));
    return tabs.filter((tab) => !removeIds.has(tab.document.documentId)).slice(-MAX_TABS);
}

export const useWorkbenchStore = create<WorkbenchStoreState>((set, get) => {
    const commit = (updater: (state: WorkbenchStoreState) => Partial<WorkbenchStoreState>) => {
        set((state) => updater(state));
        persistState(get());
    };

    return {
        sessionId: null,
        boundAt: 0,
        hydrated: false,
        mode: "closed",
        width: defaultWidth(),
        tabs: [],
        activeDocumentId: null,
        runtimeFocusSuppressed: false,

        bindSession: (sessionId) => {
            const normalizedSessionId = String(sessionId || "").trim() || null;
            const current = get();
            if (current.sessionId === normalizedSessionId && current.hydrated) return;
            persistState(current);
            for (const tab of current.tabs) {
                clearWorkbenchDocumentPayload(tab.document.documentId);
            }
            if (!normalizedSessionId) {
                set({
                    sessionId: null,
                    boundAt: 0,
                    hydrated: true,
                    mode: "closed",
                    width: defaultWidth(),
                    tabs: [],
                    activeDocumentId: null,
                    runtimeFocusSuppressed: false,
                });
                return;
            }
            const persisted = readPersistedState(normalizedSessionId);
            const overview = createSessionOverviewDocument(normalizedSessionId);
            const tabs = persisted?.tabs.length
                ? persisted.tabs.map((tab) => tab.document.kind === "session_overview"
                    ? { ...tab, document: overview }
                    : tab)
                : [{ document: overview, unread: false, openedAt: Date.now(), lastActivatedAt: Date.now() }];
            set({
                sessionId: normalizedSessionId,
                boundAt: Date.now(),
                hydrated: true,
                mode: persisted?.mode || "split",
                width: persisted?.width ?? defaultWidth(),
                tabs,
                activeDocumentId: persisted?.activeDocumentId || overview.documentId,
                runtimeFocusSuppressed: false,
            });
        },

        ensureOverview: () => {
            const { sessionId, tabs } = get();
            if (!sessionId) return;
            const overview = createSessionOverviewDocument(sessionId);
            if (tabs.some((tab) => tab.document.documentId === overview.documentId)) return;
            commit((state) => ({
                tabs: [{ document: overview, unread: false, openedAt: Date.now(), lastActivatedAt: Date.now() }, ...state.tabs],
                activeDocumentId: state.activeDocumentId || overview.documentId,
            }));
        },

        openDocument: (document, options = {}) => {
            const normalized = normalizeWorkbenchDocument(document);
            if (!normalized) return;
            const activate = options.activate !== false;
            const now = Date.now();
            commit((state) => {
                const existing = state.tabs.find((tab) => tab.document.documentId === normalized.documentId);
                const nextTab: WorkbenchTab = {
                    document: existing
                        ? { ...existing.document, ...normalized, subjectRef: { ...existing.document.subjectRef, ...normalized.subjectRef } } as WorkbenchDocument
                        : normalized,
                    unread: activate ? false : (options.markUnread ?? true),
                    openedAt: existing?.openedAt || now,
                    lastActivatedAt: activate ? now : existing?.lastActivatedAt || 0,
                };
                const nextTabs = trimTabs(
                    [...state.tabs.filter((tab) => tab.document.documentId !== normalized.documentId), nextTab],
                    normalized.documentId,
                );
                return {
                    tabs: nextTabs,
                    activeDocumentId: activate ? normalized.documentId : state.activeDocumentId,
                    mode: activate
                        ? options.mode || (state.mode === "closed" ? "split" : state.mode)
                        : state.mode,
                    runtimeFocusSuppressed: activate ? false : state.runtimeFocusSuppressed,
                };
            });
        },

        activateDocument: (documentId) => {
            const normalizedId = String(documentId || "").trim();
            if (!normalizedId) return;
            commit((state) => {
                if (!state.tabs.some((tab) => tab.document.documentId === normalizedId)) return {};
                return {
                    activeDocumentId: normalizedId,
                    tabs: state.tabs.map((tab) => tab.document.documentId === normalizedId
                        ? { ...tab, unread: false, lastActivatedAt: Date.now() }
                        : tab),
                    mode: state.mode === "closed" ? "split" : state.mode,
                };
            });
        },

        updateDocument: (document, options = {}) => {
            get().openDocument(document, {
                activate: options.activate ?? false,
                markUnread: options.markUnread ?? true,
            });
        },

        markDocumentUnavailable: (documentId, reason) => {
            commit((state) => ({
                tabs: state.tabs.map((tab) => tab.document.documentId === documentId
                    ? {
                        ...tab,
                        unread: tab.document.documentId !== state.activeDocumentId,
                        document: {
                            ...tab.document,
                            status: "unavailable",
                            unavailableReason: reason || "web.workbench.unavailable",
                        },
                    }
                    : tab),
            }));
        },

        closeDocument: (documentId) => {
            clearWorkbenchDocumentPayload(documentId);
            commit((state) => {
                const targetIndex = state.tabs.findIndex((tab) => tab.document.documentId === documentId);
                if (targetIndex < 0) return {};
                const tabs = state.tabs.filter((tab) => tab.document.documentId !== documentId);
                const nextActive = state.activeDocumentId === documentId
                    ? tabs[Math.min(targetIndex, tabs.length - 1)]?.document.documentId || null
                    : state.activeDocumentId;
                return {
                    tabs,
                    activeDocumentId: nextActive,
                    mode: tabs.length > 0 ? state.mode : "closed",
                    runtimeFocusSuppressed: tabs.length > 0 ? state.runtimeFocusSuppressed : true,
                };
            });
        },

        setMode: (mode) => {
            const normalizedMode: WorkbenchMode = ["closed", "split", "focus"].includes(mode) ? mode : "closed";
            if (normalizedMode !== "closed") get().ensureOverview();
            commit(() => ({
                mode: normalizedMode,
                runtimeFocusSuppressed: normalizedMode === "closed",
            }));
        },

        toggle: () => {
            const state = get();
            if (state.mode === "closed") {
                state.ensureOverview();
                commit(() => ({ mode: "split", runtimeFocusSuppressed: false }));
                return;
            }
            commit(() => ({ mode: "closed", runtimeFocusSuppressed: true }));
        },

        setWidth: (width) => {
            commit(() => ({ width: clampStoredWidth(width) }));
        },
    };
});

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

export function ingestWorkbenchRuntimeEvent(value: unknown) {
    const root = recordOf(value);
    const raw = recordOf(root.raw);
    const candidates = [
        root,
        recordOf(root.payload),
        recordOf(root.event),
        recordOf(recordOf(root.payload).payload),
        recordOf(recordOf(root.payload).event),
        raw,
        recordOf(raw.payload),
    ];
    const source = candidates.find((candidate) => candidate.document || candidate.workbenchDocument || candidate.workbench_document);
    if (!source) return false;
    const normalizedDocument = normalizeWorkbenchDocument(source.document || source.workbenchDocument || source.workbench_document);
    if (!normalizedDocument) return false;
    // Agent Browser owns a real external Chrome window. Workbench consumes
    // documents and governed app/canvas surfaces only; it must not turn the
    // browser screencast into a second, lower-fidelity interaction surface.
    if (normalizedDocument.kind === "browser") return true;
    const document: WorkbenchDocument = normalizedDocument;
    const topic = String(source.topic || source.name || root.topic || root.name || "").trim();
    const focusRequested = Boolean(source.focusRequested ?? source.focus_requested);
    const userInitiated = Boolean(source.userInitiated ?? source.user_initiated);
    const store = useWorkbenchStore.getState();
    const subjectRef = recordOf(document.subjectRef);
    const appRef = recordOf(subjectRef.app);
    const documentSessionId = String(
        subjectRef.sessionId
        || subjectRef.session_id
        || appRef.sessionId
        || appRef.session_id
        || "",
    ).trim();
    if (store.sessionId && documentSessionId && documentSessionId !== store.sessionId) {
        return false;
    }
    const alreadyOpen = store.tabs.some((tab) => tab.document.documentId === document.documentId);
    if (topic.endsWith("unavailable") || document.status === "unavailable") {
        if (!alreadyOpen) {
            store.openDocument(document, { activate: false, markUnread: true });
        }
        store.markDocumentUnavailable(document.documentId, document.unavailableReason);
        return true;
    }
    const requestedActivation = focusRequested || userInitiated;
    const activate = requestedActivation
        && !store.runtimeFocusSuppressed
        && !(alreadyOpen && topic.endsWith("opened"));
    store.openDocument(document, {
        activate,
        markUnread: !activate,
    });
    return true;
}
