import React from "react";
import { Check, ChevronDown } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { useLocale } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HydrationSafeClientOnly } from "@/components/ui/hydration-safe-client-only";
import { Locale } from "@/lib/locale";
import { cn } from "@/lib/utils";

function CNFlag() {
    return (
        <svg id="flag-icons-cn" viewBox="0 0 640 480" className="h-3.5 w-5 rounded-sm shadow-sm overflow-hidden flex-shrink-0 border border-slate-200/10">
            <defs>
                <path id="cn-a" fill="#ff0" d="M-.6.8 0-1 .6.8-1-.3h2z"/>
            </defs>
            <path fill="#ee1c25" d="M0 0h640v480H0z"/>
            <use xlinkHref="#cn-a" width="30" height="20" transform="matrix(71.9991 0 0 72 120 120)"/>
            <use xlinkHref="#cn-a" width="30" height="20" transform="matrix(-12.33562 -20.5871 20.58684 -12.33577 240.3 48)"/>
            <use xlinkHref="#cn-a" width="30" height="20" transform="matrix(-3.38573 -23.75998 23.75968 -3.38578 288 95.8)"/>
            <use xlinkHref="#cn-a" width="30" height="20" transform="matrix(6.5991 -23.0749 23.0746 6.59919 288 168)"/>
            <use xlinkHref="#cn-a" width="30" height="20" transform="matrix(14.9991 -18.73557 18.73533 14.99929 240 216)"/>
        </svg>
    );
}

function USFlag() {
    return (
        <svg id="flag-icons-us" viewBox="0 0 640 480" className="h-3.5 w-5 rounded-sm shadow-sm overflow-hidden flex-shrink-0 border border-slate-200/10">
            <path fill="#bd3d44" d="M0 0h640v480H0"/>
            <path stroke="#fff" strokeWidth="37" d="M0 55.3h640M0 129h640M0 203h640M0 277h640M0 351h640M0 425h640"/>
            <path fill="#192f5d" d="M0 0h364.8v258.5H0"/>
            <defs>
                <marker id="us-a" markerHeight="30" markerWidth="30">
                    <path fill="#fff" d="m14 0 9 27L0 10h28L5 27z"/>
                </marker>
            </defs>
            <path fill="none" markerMid="url(#us-a)" d="m0 0 16 11h61 61 61 61 60L47 37h61 61 60 61L16 63h61 61 61 61 60L47 89h61 61 60 61L16 115h61 61 61 61 60L47 141h61 61 60 61L16 166h61 61 61 61 60L47 192h61 61 60 61L16 218h61 61 61 61 60z"/>
        </svg>
    );
}

const OPTIONS: Array<{
    value: Locale;
    flag: React.ReactNode;
    label: string;
    switchKey: "layout.locale.switchToChinese" | "layout.locale.switchToEnglish";
}> = [
    { value: "zh-CN", flag: <CNFlag />, label: "layout.locale.label.zhCN", switchKey: "layout.locale.switchToChinese" },
    { value: "en", flag: <USFlag />, label: "layout.locale.label.en", switchKey: "layout.locale.switchToEnglish" },
];

export function LocaleToggle() {
    const { locale, setLocale } = useLocale();
    const t = useT();
    const currentOption = OPTIONS.find((option) => option.value === locale) || OPTIONS[0];
    const trigger = (
        <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t("layout.locale.switcher")}
            title={t("layout.locale.switcher")}
            className="h-9 rounded-full border-slate-200 bg-white/90 px-3 shadow-sm hover:bg-slate-50"
        >
            <span className="mr-2 flex items-center">{currentOption.flag}</span>
            <span className="text-xs font-semibold tracking-[0.04em] text-slate-700">
                {t(currentOption.label)}
            </span>
            <ChevronDown className="ml-2 h-4 w-4 text-slate-500" />
        </Button>
    );

    return (
        <HydrationSafeClientOnly fallback={trigger}>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                    {OPTIONS.map((option) => {
                        const active = option.value === locale;
                        return (
                            <DropdownMenuItem
                                key={option.value}
                                onSelect={() => setLocale(option.value)}
                                aria-label={t(option.switchKey)}
                                className={cn(
                                    "gap-3 rounded-lg px-3 py-2.5",
                                    active ? "bg-slate-100 text-slate-900" : "text-slate-700",
                                )}
                            >
                                <span className="flex items-center">{option.flag}</span>
                                <span className="flex-1 text-sm font-medium">{t(option.label)}</span>
                                {active ? <Check className="h-4 w-4 text-slate-900" /> : null}
                            </DropdownMenuItem>
                        );
                    })}
                </DropdownMenuContent>
            </DropdownMenu>
        </HydrationSafeClientOnly>
    );
}
