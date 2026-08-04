"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { Bot, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { TopbarGlowActionButton } from "@v8/product-ui";
import { useT } from "@/components/providers/LocaleProvider";

const loadRPAQuickPanel = () => import("@/components/rpa/RPAQuickPanel")
    .then((module) => module.RPAQuickPanel);

const RPAQuickPanel = dynamic(
    loadRPAQuickPanel,
    {
        loading: () => <div className="h-48 animate-pulse rounded-2xl bg-muted/35" />,
        ssr: false,
    },
);

export function RpaTopbarOverlay() {
    const pathname = usePathname();

    if (pathname === "/rpa") return null;
    return <RpaTopbarOverlayContent key={pathname} />;
}

function RpaTopbarOverlayContent() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [activated, setActivated] = useState(false);
    const triggerRef = useRef<HTMLElement | null>(null);

    useEffect(() => {
        void loadRPAQuickPanel();
    }, []);

    useEffect(() => {
        if (!open) return;
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            setOpen(false);
            window.requestAnimationFrame(() => triggerRef.current?.focus());
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, [open]);

    const close = () => {
        setOpen(false);
        window.requestAnimationFrame(() => triggerRef.current?.focus());
    };

    return (
        <>
            <TopbarGlowActionButton
                ref={triggerRef}
                tone="emerald"
                aria-label={t("web.rpa.title")}
                aria-expanded={open}
                aria-haspopup="dialog"
                title={t("web.rpa.title")}
                onClick={() => {
                    if (!activated) setActivated(true);
                    setOpen((current) => !current);
                }}
            >
                <Bot />
                <span className="sr-only">{t("web.rpa.title")}</span>
            </TopbarGlowActionButton>

            {activated ? createPortal(
                <div
                    className={open
                        ? "fixed inset-0 z-[140] bg-slate-950/12 backdrop-blur-[1px] dark:bg-black/24"
                        : "hidden"}
                    aria-hidden={!open}
                    onPointerDown={(event) => {
                        if (event.target === event.currentTarget) close();
                    }}
                >
                    <section
                        role="dialog"
                        aria-modal="true"
                        aria-label={t("web.rpa.title")}
                        className="absolute right-[max(12px,env(safe-area-inset-right))] top-[max(58px,calc(env(safe-area-inset-top)+58px))] flex max-h-[min(78dvh,760px)] w-[min(820px,calc(100vw-24px))] flex-col overflow-hidden rounded-[24px] border border-border/70 bg-background/94 shadow-[0_28px_90px_rgba(15,23,42,0.22)] backdrop-blur-2xl dark:shadow-[0_28px_100px_rgba(0,0,0,0.55)]"
                        onPointerDown={(event) => event.stopPropagation()}
                    >
                        <button
                            type="button"
                            className="absolute right-3 top-3 z-10 inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45"
                            aria-label={t("web.generated.fbd8cee012")}
                            onClick={close}
                        >
                            <X className="h-4 w-4" />
                        </button>
                        <div className="min-h-0 flex-1 overflow-hidden pr-8">
                            <RPAQuickPanel embedded />
                        </div>
                    </section>
                </div>,
                document.body,
            ) : null}
        </>
    );
}
