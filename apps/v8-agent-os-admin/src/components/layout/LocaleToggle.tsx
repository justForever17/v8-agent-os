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
        <svg viewBox="0 0 30 20" className="h-3.5 w-5 rounded-sm shadow-sm overflow-hidden flex-shrink-0 border border-slate-200/10">
            <rect width="30" height="20" fill="#de2110" />
            <polygon points="3,2 4,5 1,3 5,3 2,5" fill="#f8e31b" transform="translate(1.5, 3) scale(0.6)" />
            <polygon points="3,2 4,5 1,3 5,3 2,5" fill="#f8e31b" transform="translate(7.5, 1.5) rotate(30) scale(0.18)" />
            <polygon points="3,2 4,5 1,3 5,3 2,5" fill="#f8e31b" transform="translate(9, 3.5) rotate(45) scale(0.18)" />
            <polygon points="3,2 4,5 1,3 5,3 2,5" fill="#f8e31b" transform="translate(9, 6.5) rotate(0) scale(0.18)" />
            <polygon points="3,2 4,5 1,3 5,3 2,5" fill="#f8e31b" transform="translate(7.5, 8.5) rotate(30) scale(0.18)" />
        </svg>
    );
}

function USFlag() {
    return (
        <svg viewBox="0 0 7410 3900" className="h-3.5 w-5 rounded-sm shadow-sm overflow-hidden flex-shrink-0 border border-slate-200/10">
            <rect width="7410" height="3900" fill="#b22234" />
            <path d="M0,300H7410M0,900H7410M0,1500H7410M0,2100H7410M0,2700H7410M0,3300H7410" stroke="#fff" strokeWidth="300" />
            <rect width="2964" height="2100" fill="#3c3b6e" />
            <g fill="#fff">
                <circle cx="247" cy="175" r="45"/><circle cx="741" cy="175" r="45"/><circle cx="1235" cy="175" r="45"/><circle cx="1729" cy="175" r="45"/><circle cx="2223" cy="175" r="45"/><circle cx="2717" cy="175" r="45"/>
                <circle cx="494" cy="350" r="45"/><circle cx="988" cy="350" r="45"/><circle cx="1482" cy="350" r="45"/><circle cx="1976" cy="350" r="45"/><circle cx="2470" cy="350" r="45"/>
                <circle cx="247" cy="525" r="45"/><circle cx="741" cy="525" r="45"/><circle cx="1235" cy="525" r="45"/><circle cx="1729" cy="525" r="45"/><circle cx="2223" cy="525" r="45"/><circle cx="2717" cy="525" r="45"/>
                <circle cx="494" cy="700" r="45"/><circle cx="988" cy="700" r="45"/><circle cx="1482" cy="700" r="45"/><circle cx="1976" cy="700" r="45"/><circle cx="2470" cy="700" r="45"/>
                <circle cx="247" cy="875" r="45"/><circle cx="741" cy="875" r="45"/><circle cx="1235" cy="875" r="45"/><circle cx="1729" cy="875" r="45"/><circle cx="2223" cy="875" r="45"/><circle cx="2717" cy="875" r="45"/>
                <circle cx="494" cy="1050" r="45"/><circle cx="988" cy="1050" r="45"/><circle cx="1482" cy="1050" r="45"/><circle cx="1976" cy="1050" r="45"/><circle cx="2470" cy="1050" r="45"/>
                <circle cx="247" cy="1225" r="45"/><circle cx="741" cy="1225" r="45"/><circle cx="1235" cy="1225" r="45"/><circle cx="1729" cy="1225" r="45"/><circle cx="2223" cy="1225" r="45"/><circle cx="2717" cy="1225" r="45"/>
                <circle cx="494" cy="1400" r="45"/><circle cx="988" cy="1400" r="45"/><circle cx="1482" cy="1400" r="45"/><circle cx="1976" cy="1400" r="45"/><circle cx="2470" cy="1400" r="45"/>
                <circle cx="247" cy="1575" r="45"/><circle cx="741" cy="1575" r="45"/><circle cx="1235" cy="1575" r="45"/><circle cx="1729" cy="1575" r="45"/><circle cx="2223" cy="1575" r="45"/><circle cx="2717" cy="1575" r="45"/>
                <circle cx="494" cy="1750" r="45"/><circle cx="988" cy="1750" r="45"/><circle cx="1482" cy="1750" r="45"/><circle cx="1976" cy="1750" r="45"/><circle cx="2470" cy="1750" r="45"/>
                <circle cx="247" cy="1925" r="45"/><circle cx="741" cy="1925" r="45"/><circle cx="1235" cy="1925" r="45"/><circle cx="1729" cy="1925" r="45"/><circle cx="2223" cy="1925" r="45"/><circle cx="2717" cy="1925" r="45"/>
            </g>
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
