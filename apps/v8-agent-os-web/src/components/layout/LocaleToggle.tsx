"use client";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { Locale } from "@/lib/locale";
import { useLocale } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{ value: Locale; label: string }> = [
    { value: "zh-CN", label: "中" },
    { value: "en", label: "EN" },
];

export function LocaleToggle() {
    const { locale, setLocale } = useLocale();
    const t = useT();

    return (
        <div
            className="inline-flex h-9 items-center rounded-full border border-border/60 bg-background/78 p-1 shadow-sm backdrop-blur-lg dark:border-white/10"
            role="group"
            aria-label={t("web.generated.b989b868f1")}
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
                        aria-label={option.value === "zh-CN" ? t("web.generated.afd6903b7c") : t("web.generated.83334919ce")}
                        title={option.value === "zh-CN" ? t("web.generated.afd6903b7c") : t("web.generated.83334919ce")}
                        className={cn(
                            "h-7 rounded-full px-2.5 text-[11px] font-semibold tracking-[0.08em] transition-all",
                            active
                                ? "bg-primary text-primary-foreground shadow-sm"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {option.label}
                    </Button>
                );
            })}
        </div>
    );
}
