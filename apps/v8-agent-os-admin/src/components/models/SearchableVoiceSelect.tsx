"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, LoaderCircle, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type SearchableVoiceOption = {
    value: string;
    label: string;
    availability?: "available" | "confirmed" | "pending_activation" | string;
    deletable?: boolean;
};

type SearchableVoiceSelectProps = {
    value: string;
    options: SearchableVoiceOption[];
    placeholder: string;
    searchPlaceholder: string;
    emptyLabel: string;
    onValueChange: (value: string) => void;
    disabled?: boolean;
    invalid?: boolean;
    deleteLabel?: string;
    deletingValue?: string | null;
    onDelete?: (option: SearchableVoiceOption) => void;
};

function compactSearchText(value: string): string {
    return value
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLocaleLowerCase()
        .replace(/[\s._/\\-]+/g, "");
}

function fuzzyScore(option: SearchableVoiceOption, rawQuery: string): number {
    const query = compactSearchText(rawQuery);
    if (!query) return 0;
    const target = compactSearchText(`${option.label} ${option.value}`);
    if (target.startsWith(query)) return 0;
    const containsAt = target.indexOf(query);
    if (containsAt >= 0) return 10 + containsAt;

    let targetIndex = 0;
    let gaps = 0;
    for (const character of query) {
        const matchAt = target.indexOf(character, targetIndex);
        if (matchAt < 0) return Number.POSITIVE_INFINITY;
        gaps += matchAt - targetIndex;
        targetIndex = matchAt + 1;
    }
    return 100 + gaps;
}

export function SearchableVoiceSelect({
    value,
    options,
    placeholder,
    searchPlaceholder,
    emptyLabel,
    onValueChange,
    disabled = false,
    invalid = false,
    deleteLabel = "Delete voice",
    deletingValue = null,
    onDelete,
}: SearchableVoiceSelectProps) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const [activeIndex, setActiveIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement | null>(null);
    const optionRefs = useRef<Array<HTMLDivElement | null>>([]);
    const listboxId = useId();
    const selected = options.find((option) => option.value === value);
    const filteredOptions = useMemo(
        () => options
            .map((option, originalIndex) => ({ option, originalIndex, score: fuzzyScore(option, query) }))
            .filter((entry) => Number.isFinite(entry.score))
            .sort((left, right) => left.score - right.score || left.originalIndex - right.originalIndex)
            .map((entry) => entry.option),
        [options, query],
    );

    useEffect(() => {
        if (!open) return;
        const frame = requestAnimationFrame(() => inputRef.current?.focus());
        return () => cancelAnimationFrame(frame);
    }, [open]);

    useEffect(() => {
        optionRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
    }, [activeIndex]);

    const choose = (nextValue: string) => {
        onValueChange(nextValue);
        setQuery("");
        setOpen(false);
    };

    return (
        <DropdownMenu open={open} onOpenChange={(nextOpen) => {
            setOpen(nextOpen);
            setActiveIndex(0);
            if (!nextOpen) setQuery("");
        }}>
            <DropdownMenuTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    aria-controls={listboxId}
                    aria-invalid={invalid || undefined}
                    disabled={disabled}
                    className={cn(
                        "h-10 w-full justify-between px-3 font-normal",
                        invalid && "border-destructive text-destructive focus-visible:ring-destructive",
                    )}
                >
                    <span className="min-w-0 truncate text-left">{selected?.label || value || placeholder}</span>
                    <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-60" aria-hidden="true" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
                align="start"
                className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-[18rem] max-w-[min(34rem,calc(100vw-2rem))] border-border bg-popover p-1.5 text-popover-foreground"
            >
                <div className="relative p-1" onPointerDown={(event) => event.stopPropagation()}>
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                    <Input
                        ref={inputRef}
                        value={query}
                        onChange={(event) => {
                            setQuery(event.target.value);
                            setActiveIndex(0);
                        }}
                        onKeyDown={(event) => {
                            if (event.key === "Escape") {
                                event.preventDefault();
                                setOpen(false);
                                return;
                            }
                            if (event.key === "Tab") return;
                            event.stopPropagation();
                            if (event.key === "ArrowDown") {
                                event.preventDefault();
                                if (filteredOptions.length > 0) {
                                    setActiveIndex((current) => Math.min(current + 1, filteredOptions.length - 1));
                                }
                            } else if (event.key === "ArrowUp") {
                                event.preventDefault();
                                setActiveIndex((current) => Math.max(current - 1, 0));
                            } else if (event.key === "Enter" && filteredOptions[activeIndex]) {
                                event.preventDefault();
                                choose(filteredOptions[activeIndex].value);
                            }
                        }}
                        placeholder={searchPlaceholder}
                        aria-controls={listboxId}
                        aria-activedescendant={filteredOptions[activeIndex] ? `${listboxId}-${activeIndex}` : undefined}
                        className="h-9 pl-9"
                    />
                </div>
                <div id={listboxId} role="listbox" className="max-h-72 overflow-y-auto overscroll-contain py-1">
                    {filteredOptions.length > 0 ? filteredOptions.map((option, index) => (
                        <DropdownMenuItem
                            id={`${listboxId}-${index}`}
                            key={option.value}
                            ref={(node) => { optionRefs.current[index] = node; }}
                            role="option"
                            aria-selected={option.value === value}
                            onMouseMove={() => setActiveIndex(index)}
                            onSelect={() => choose(option.value)}
                            className={cn(
                                "flex min-h-10 cursor-pointer gap-2 text-foreground focus:bg-accent",
                                index === activeIndex && "bg-accent",
                            )}
                        >
                            <span className="min-w-0 flex-1 truncate">{option.label}</span>
                            {option.value === value ? <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" /> : null}
                            {option.deletable && onDelete ? (
                                <button
                                    type="button"
                                    aria-label={`${deleteLabel}: ${option.label}`}
                                    title={deleteLabel}
                                    disabled={deletingValue === option.value}
                                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-transparent text-muted-foreground transition-colors hover:bg-transparent hover:text-rose-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                                    onPointerDown={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                    }}
                                    onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        onDelete(option);
                                    }}
                                >
                                    {deletingValue === option.value ? (
                                        <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                                    ) : (
                                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                                    )}
                                </button>
                            ) : null}
                        </DropdownMenuItem>
                    )) : (
                        <p className="px-3 py-6 text-center text-sm text-muted-foreground" role="status">{emptyLabel}</p>
                    )}
                </div>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
