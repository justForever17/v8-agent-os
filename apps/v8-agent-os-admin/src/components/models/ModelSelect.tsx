"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export type AdminModelSelectOption = {
    id?: string;
    modelRef?: string;
    providerId?: string;
    modelId?: string;
    name?: string;
    type?: string;
    provider?: {
        id?: string;
        name?: string | null;
    } | null;
    providerName?: string | null;
};

type ResolvedModelValue = {
    selectValue: string;
    status: "empty" | "exact" | "legacy_unique" | "legacy_ambiguous" | "stale";
    message: string;
};

export function modelOptionValue(model: AdminModelSelectOption): string {
    const direct = String(model.modelRef || model.id || "").trim();
    if (direct) return direct;
    const providerId = String(model.providerId || model.provider?.id || model.providerName || "").trim();
    const modelId = String(model.modelId || "").trim();
    if (providerId && modelId) return `${providerId}::${encodeURIComponent(modelId)}`;
    return modelId;
}

export function modelOptionLabel(model: AdminModelSelectOption): string {
    const modelId = String(model.modelId || model.name || model.id || "").trim();
    const providerName = String(model.provider?.name || model.providerName || model.providerId || "").trim();
    return providerName ? `${modelId} (${providerName})` : modelId;
}

export function resolveModelSelectValue(
    value: string | null | undefined,
    models: AdminModelSelectOption[],
    emptyValue: string,
): ResolvedModelValue {
    const raw = String(value || "").trim();
    if (!raw || raw === emptyValue) {
        return { selectValue: emptyValue, status: "empty", message: "" };
    }
    const exact = models.find((model) => modelOptionValue(model) === raw);
    if (exact) {
        return { selectValue: raw, status: "exact", message: "" };
    }
    const legacyMatches = models.filter((model) => String(model.modelId || "").trim() === raw);
    if (legacyMatches.length === 1) {
        return {
            selectValue: modelOptionValue(legacyMatches[0]),
            status: "legacy_unique",
            message: `当前配置使用旧模型名 ${raw}，已匹配到唯一供应商；重新保存后会写入 provider-qualified modelRef。`,
        };
    }
    if (legacyMatches.length > 1) {
        return {
            selectValue: emptyValue,
            status: "legacy_ambiguous",
            message: `当前配置使用旧模型名 ${raw}，但存在 ${legacyMatches.length} 个同名模型；请重新选择供应商后的模型。`,
        };
    }
    return {
        selectValue: raw,
        status: "stale",
        message: `当前配置的模型 ${raw} 不在模型目录中；请确认模型仍存在或重新选择。`,
    };
}

export function ModelSelect({
    models,
    value,
    onValueChange,
    placeholder = "选择模型",
    emptyValue = "__empty__",
    emptyLabel,
    emptyOutputValue = "",
    showCompatibilityHint = true,
    className,
}: {
    models: AdminModelSelectOption[];
    value?: string | null;
    onValueChange: (value: string) => void;
    placeholder?: string;
    emptyValue?: string;
    emptyLabel?: string;
    emptyOutputValue?: string;
    showCompatibilityHint?: boolean;
    className?: string;
}) {
    const resolved = resolveModelSelectValue(value, models, emptyValue);
    const seen = new Set<string>();
    const options = models
        .map((model) => ({ model, value: modelOptionValue(model), label: modelOptionLabel(model) }))
        .filter((item) => {
            if (!item.value || seen.has(item.value)) return false;
            seen.add(item.value);
            return true;
        });
    const hasStaleItem = resolved.status === "stale" && resolved.selectValue && !seen.has(resolved.selectValue);

    return (
        <div className={className || "space-y-2"}>
            <Select
                value={resolved.selectValue}
                onValueChange={(next) => onValueChange(next === emptyValue ? emptyOutputValue : next)}
            >
                <SelectTrigger className="w-full">
                    <SelectValue placeholder={placeholder} />
                </SelectTrigger>
                <SelectContent>
                    {emptyLabel ? <SelectItem value={emptyValue}>{emptyLabel}</SelectItem> : null}
                    {hasStaleItem ? (
                        <SelectItem value={resolved.selectValue} disabled>
                            当前配置：{resolved.selectValue}
                        </SelectItem>
                    ) : null}
                    {options.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                            {item.label}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
            {showCompatibilityHint && resolved.message ? (
                <p className="text-xs leading-5 text-amber-700">{resolved.message}</p>
            ) : null}
        </div>
    );
}
