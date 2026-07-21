"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ClipboardPaste, Copy, Link2, MousePointer2, PanelRightOpen, Scissors } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

type TextControl = HTMLInputElement | HTMLTextAreaElement;

type ContextMenuState = {
    x: number;
    y: number;
    control: TextControl | null;
    contentEditable: HTMLElement | null;
    editable: boolean;
    selectionStart: number | null;
    selectionEnd: number | null;
    selectionRange: Range | null;
    selectedText: string;
    selectionScope: Element | null;
    link: HTMLAnchorElement | null;
    workbenchTrigger: HTMLElement | null;
};

const TEXT_INPUT_TYPES = new Set(["email", "password", "search", "tel", "text", "url"]);
const TEXT_SCOPE_SELECTOR = [
    "[data-v8-select-scope]",
    ".prose",
    "article",
    "blockquote",
    "pre",
    "code",
    "p",
    "li",
    "td",
    "th",
    "dt",
    "dd",
].join(",");
const NON_SELECTABLE_SURFACE_SELECTOR = [
    "button",
    "[role='button']",
    "[role='menuitem']",
    "[role='switch']",
    "[role='checkbox']",
    "[role='tab']",
    "[role='status']",
    "[role='progressbar']",
    "[data-v8-context-menu-ignore]",
    "[data-v8-context-nonselectable]",
].join(",");

function textControlFromTarget(target: Element): TextControl | null {
    const control = target.closest("input, textarea");
    if (control instanceof HTMLTextAreaElement) return control;
    if (control instanceof HTMLInputElement && TEXT_INPUT_TYPES.has(control.type.toLowerCase())) return control;
    return null;
}

function editableFromTarget(target: Element): HTMLElement | null {
    const editable = target.closest<HTMLElement>("[contenteditable]:not([contenteditable='false'])");
    return editable?.isContentEditable ? editable : null;
}

function selectionOffsets(control: TextControl) {
    try {
        return {
            start: typeof control.selectionStart === "number" ? control.selectionStart : null,
            end: typeof control.selectionEnd === "number" ? control.selectionEnd : null,
        };
    } catch {
        return { start: null, end: null };
    }
}

function selectedControlText(control: TextControl, start: number | null, end: number | null) {
    if (start === null || end === null || end <= start) return "";
    return control.value.slice(start, end);
}

function closestWorkbenchTrigger(target: Element) {
    const direct = target.closest<HTMLElement>("[data-v8-context-open-workbench]");
    if (direct) return direct;
    return target
        .closest<HTMLElement>("[data-v8-context-resource]")
        ?.querySelector<HTMLElement>("[data-v8-context-open-workbench]") || null;
}

function selectionScopeFromTarget(target: Element) {
    return target.closest(TEXT_SCOPE_SELECTOR);
}

async function writeClipboard(value: string) {
    if (!value) return;
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(value);
            return;
        } catch {
            // Fall through to the local selection-based copy path.
        }
    }
    const fallback = document.createElement("textarea");
    fallback.value = value;
    fallback.setAttribute("readonly", "true");
    fallback.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(fallback);
    fallback.select();
    document.execCommand("copy");
    fallback.remove();
}

function dispatchInput(target: HTMLElement, inputType: string, data: string | null) {
    target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType, data }));
}

function restoreContentEditableSelection(state: ContextMenuState) {
    if (!state.contentEditable || !state.selectionRange) return;
    state.contentEditable.focus();
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(state.selectionRange.cloneRange());
}

function replaceSelection(state: ContextMenuState, value: string, inputType: string) {
    if (state.control && state.editable) {
        const start = state.selectionStart ?? state.control.value.length;
        const end = state.selectionEnd ?? start;
        state.control.focus();
        state.control.setRangeText(value, start, end, "end");
        dispatchInput(state.control, inputType, value || null);
        return;
    }
    if (!state.contentEditable || !state.editable) return;
    restoreContentEditableSelection(state);
    if (document.execCommand("insertText", false, value)) return;
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range) return;
    range.deleteContents();
    if (value) {
        const node = document.createTextNode(value);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        selection?.removeAllRanges();
        selection?.addRange(range);
    }
    dispatchInput(state.contentEditable, inputType, value || null);
}

function selectAll(state: ContextMenuState) {
    if (state.control) {
        state.control.focus();
        state.control.select();
        return;
    }
    const scope = state.contentEditable || state.selectionScope;
    if (!scope) return;
    state.contentEditable?.focus();
    const range = document.createRange();
    range.selectNodeContents(scope);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
}

type MenuItemProps = {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    disabled?: boolean;
    onSelect: () => void | Promise<void>;
};

function MenuItem({ icon: Icon, label, disabled, onSelect }: MenuItemProps) {
    return (
        <button
            type="button"
            role="menuitem"
            disabled={disabled}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground outline-none transition hover:bg-accent focus-visible:bg-accent disabled:pointer-events-none disabled:opacity-40"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => void onSelect()}
        >
            <Icon className="h-4 w-4 text-muted-foreground" />
            <span>{label}</span>
        </button>
    );
}

export function AppContextMenu() {
    const t = useT();
    const [state, setState] = useState<ContextMenuState | null>(null);
    const menuRef = useRef<HTMLDivElement>(null);

    const close = useCallback(() => setState(null), []);

    useEffect(() => {
        document.documentElement.dataset.v8ContextMenu = "ready";
        return () => {
            delete document.documentElement.dataset.v8ContextMenu;
        };
    }, []);

    useEffect(() => {
        const open = (event: MouseEvent) => {
            if (event.defaultPrevented || !(event.target instanceof Element)) return;
            const target = event.target;
            const control = textControlFromTarget(target);
            const contentEditable = editableFromTarget(target);
            if (!control && !contentEditable && target.closest(NON_SELECTABLE_SURFACE_SELECTOR)) return;
            const offsets = control ? selectionOffsets(control) : { start: null, end: null };
            const pageSelection = window.getSelection();
            const selectedText = control
                ? selectedControlText(control, offsets.start, offsets.end)
                : String(pageSelection?.toString() || "");
            const selectionRange = contentEditable && pageSelection?.rangeCount
                ? pageSelection.getRangeAt(0).cloneRange()
                : null;
            const link = target.closest<HTMLAnchorElement>("a[href]");
            const workbenchTrigger = closestWorkbenchTrigger(target);
            const selectionScope = selectionScopeFromTarget(target);
            if (!control && !contentEditable && !link && !workbenchTrigger && !selectionScope) return;

            event.preventDefault();
            const editable = Boolean(
                (control && !control.disabled && !control.readOnly)
                || (contentEditable && contentEditable.isContentEditable),
            );
            setState({
                x: event.clientX,
                y: event.clientY,
                control,
                contentEditable,
                editable,
                selectionStart: offsets.start,
                selectionEnd: offsets.end,
                selectionRange,
                selectedText,
                selectionScope,
                link,
                workbenchTrigger,
            });
        };
        document.addEventListener("contextmenu", open);
        return () => document.removeEventListener("contextmenu", open);
    }, []);

    useEffect(() => {
        if (!state) return;
        const closeOnPointer = (event: PointerEvent) => {
            if (!menuRef.current?.contains(event.target as Node)) close();
        };
        const closeOnKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") close();
        };
        window.addEventListener("pointerdown", closeOnPointer);
        window.addEventListener("blur", close);
        window.addEventListener("resize", close);
        window.addEventListener("scroll", close, true);
        window.addEventListener("keydown", closeOnKey);
        return () => {
            window.removeEventListener("pointerdown", closeOnPointer);
            window.removeEventListener("blur", close);
            window.removeEventListener("resize", close);
            window.removeEventListener("scroll", close, true);
            window.removeEventListener("keydown", closeOnKey);
        };
    }, [close, state]);

    useEffect(() => {
        if (!state || !menuRef.current) return;
        const frame = requestAnimationFrame(() => {
            const rect = menuRef.current?.getBoundingClientRect();
            if (!rect) return;
            const x = Math.max(8, Math.min(state.x, window.innerWidth - rect.width - 8));
            const y = Math.max(8, Math.min(state.y, window.innerHeight - rect.height - 8));
            if (x !== state.x || y !== state.y) setState((current) => current ? { ...current, x, y } : current);
            menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
        });
        return () => cancelAnimationFrame(frame);
    }, [state]);

    const onMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || []);
        if (!items.length) return;
        event.preventDefault();
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home"
            ? 0
            : event.key === "End"
                ? items.length - 1
                : event.key === "ArrowDown"
                    ? (current + 1 + items.length) % items.length
                    : (current - 1 + items.length) % items.length;
        items[next]?.focus({ preventScroll: true });
    }, []);

    if (!state || typeof document === "undefined") return null;
    const hasEditableMenu = Boolean(state.control || state.contentEditable);
    const hasSelectedText = Boolean(state.selectedText);
    const linkValue = state.link?.href || "";

    return createPortal(
        <div
            ref={menuRef}
            role="menu"
            aria-label={t("web.contextMenu.label")}
            className={cn(
                "fixed z-[120] w-[184px] overflow-hidden rounded-xl border border-border/70 bg-background/95 p-1 text-sm shadow-2xl backdrop-blur-xl",
                "animate-in fade-in-0 zoom-in-95 duration-150",
            )}
            style={{ left: state.x, top: state.y, transformOrigin: "top left" }}
            onContextMenu={(event) => event.preventDefault()}
            onKeyDown={onMenuKeyDown}
        >
            {hasEditableMenu ? (
                <>
                    {state.editable ? <MenuItem icon={Scissors} label={t("web.contextMenu.cut")} disabled={!hasSelectedText} onSelect={async () => {
                        await writeClipboard(state.selectedText);
                        replaceSelection(state, "", "deleteByCut");
                        close();
                    }} /> : null}
                    <MenuItem icon={Copy} label={t("web.contextMenu.copy")} disabled={!hasSelectedText} onSelect={async () => {
                        await writeClipboard(state.selectedText);
                        close();
                    }} />
                    {state.editable ? <MenuItem icon={ClipboardPaste} label={t("web.contextMenu.paste")} onSelect={async () => {
                        try {
                            const value = await navigator.clipboard?.readText?.();
                            if (typeof value === "string") replaceSelection(state, value, "insertFromPaste");
                        } catch {
                            // Clipboard permissions can be denied outside the managed desktop shell.
                        }
                        close();
                    }} /> : null}
                    <div className="-mx-1 my-1 h-px bg-border/70" />
                    <MenuItem icon={MousePointer2} label={t("web.contextMenu.selectAll")} onSelect={() => {
                        selectAll(state);
                        close();
                    }} />
                </>
            ) : (
                <>
                    <MenuItem icon={Copy} label={t("web.contextMenu.copy")} disabled={!hasSelectedText} onSelect={async () => {
                        await writeClipboard(state.selectedText);
                        close();
                    }} />
                    {linkValue ? <MenuItem icon={Link2} label={t("web.contextMenu.copyLink")} onSelect={async () => {
                        await writeClipboard(linkValue);
                        close();
                    }} /> : null}
                    {state.workbenchTrigger ? <MenuItem icon={PanelRightOpen} label={t("web.contextMenu.openInWorkbench")} onSelect={() => {
                        state.workbenchTrigger?.click();
                        close();
                    }} /> : null}
                    <div className="-mx-1 my-1 h-px bg-border/70" />
                    <MenuItem icon={MousePointer2} label={t("web.contextMenu.selectAll")} disabled={!state.selectionScope} onSelect={() => {
                        selectAll(state);
                        close();
                    }} />
                </>
            )}
        </div>,
        document.body,
    );
}
