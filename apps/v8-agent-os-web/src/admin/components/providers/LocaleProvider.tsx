"use client";

import { useMemo } from "react";
import { useLocale as useProductLocale } from "@/components/providers/LocaleProvider";
import { resolveText, type TranslationParams } from "@admin/lib/locale";

// Admin keeps its catalog; language and persistence belong to the Product Web root.
export function useLocale() {
    const { locale, setLocale } = useProductLocale();
    return useMemo(() => {
        const translate = (value: string, params?: TranslationParams) => value ? resolveText(locale, value, params) : value;
        return { locale, setLocale, t: translate, resolveText: translate };
    }, [locale, setLocale]);
}

export function useT() { return useLocale().t; }
export function useResolveText() { return useLocale().resolveText; }
