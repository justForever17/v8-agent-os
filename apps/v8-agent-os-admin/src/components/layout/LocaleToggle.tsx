"use client";

import { Button } from "@/components/ui/button";
import { useLocale } from "@/components/providers/LocaleProvider";
import { Locale, lt } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { useT } from "@/components/providers/LocaleProvider";

const OPTIONS: Array<{ value: Locale; label: string }> = [
    { value: "zh-CN", label: "中" },
    { value: "en", label: "EN" },
];

export function LocaleToggle() {
    const { locale, setLocale } = useLocale();
    const t = useT();

    return (
        <div
            className="inline-flex h-9 items-center rounded-full border border-slate-200 bg-white/90 p-1 shadow-sm"
            role="group"
            aria-label={t(lt("语言切换", "Language switcher"))}
        >
            {OPTIONS.map((option) => {
                const active = option.value === locale;
                return (
                    <Button
                        key={option.value}
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setLocale(option.value)}
                        aria-pressed={active}
                        aria-label={option.value === "zh-CN" ? t(lt("切换到中文", "Switch to Chinese")) : t(lt("切换到英文", "Switch to English"))}
                        title={option.value === "zh-CN" ? t(lt("切换到中文", "Switch to Chinese")) : t(lt("切换到英文", "Switch to English"))}
                        className={cn(
                            "h-7 rounded-full px-2.5 text-[11px] font-semibold tracking-[0.08em] transition-all",
                            active
                                ? "bg-slate-900 text-white shadow-sm hover:bg-slate-900"
                                : "text-slate-500 hover:text-slate-900",
                        )}
                    >
                        {option.label}
                    </Button>
                );
            })}
        </div>
    );
}
