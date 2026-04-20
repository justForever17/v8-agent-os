"use client";

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
import { Locale } from "@/lib/locale";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{
    value: Locale;
    flag: string;
    label: string;
    switchKey: "layout.locale.switchToChinese" | "layout.locale.switchToEnglish";
}> = [
    { value: "zh-CN", flag: "🇨🇳", label: "layout.locale.label.zhCN", switchKey: "layout.locale.switchToChinese" },
    { value: "en", flag: "🇺🇸", label: "layout.locale.label.en", switchKey: "layout.locale.switchToEnglish" },
];

export function LocaleToggle() {
    const { locale, setLocale } = useLocale();
    const t = useT();
    const currentOption = OPTIONS.find((option) => option.value === locale) || OPTIONS[0];

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={t("layout.locale.switcher")}
                    title={t("layout.locale.switcher")}
                    className="h-9 rounded-full border-slate-200 bg-white/90 px-3 shadow-sm hover:bg-slate-50"
                >
                    <span className="mr-2 text-base leading-none">{currentOption.flag}</span>
                    <span className="text-xs font-semibold tracking-[0.04em] text-slate-700">
                        {t(currentOption.label)}
                    </span>
                    <ChevronDown className="ml-2 h-4 w-4 text-slate-500" />
                </Button>
            </DropdownMenuTrigger>
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
                            <span className="text-base leading-none">{option.flag}</span>
                            <span className="flex-1 text-sm font-medium">{t(option.label)}</span>
                            {active ? <Check className="h-4 w-4 text-slate-900" /> : null}
                        </DropdownMenuItem>
                    );
                })}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
