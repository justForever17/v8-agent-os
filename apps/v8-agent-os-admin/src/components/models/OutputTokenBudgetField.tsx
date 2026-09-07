"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useT } from "@/components/providers/LocaleProvider";

export function OutputTokenBudgetField({ id, model }: {
    id: string;
    model?: { outputTokenMode?: "auto" | "fixed"; maxTokens?: number | null } | null;
}) {
    const t = useT();
    const [mode, setMode] = useState<"auto" | "fixed">(() => model?.outputTokenMode || (model?.maxTokens ? "fixed" : "auto"));
    const [value, setValue] = useState(() => String(model?.maxTokens ?? ""));
    return <div className="grid min-w-0 gap-2">
        <Label htmlFor={id}>{t("app.admin.dashboard.model.hub.page.k1f9a045b")}</Label>
        <input type="hidden" name="outputTokenMode" value={mode} />
        <Select value={mode} onValueChange={(next) => setMode(next as "auto" | "fixed")}>
            <SelectTrigger aria-label={t("app.admin.dashboard.model.hub.page.outputTokenMode")}><SelectValue /></SelectTrigger>
            <SelectContent>
                <SelectItem value="auto">{t("app.admin.dashboard.model.hub.page.outputTokenAuto")}</SelectItem>
                <SelectItem value="fixed">{t("app.admin.dashboard.model.hub.page.outputTokenFixed")}</SelectItem>
            </SelectContent>
        </Select>
        <Input id={id} name="maxTokens" type="number" min={1} step={1}
            disabled={mode === "auto"} required={mode === "fixed"}
            value={mode === "auto" ? "" : value} onChange={(event) => setValue(event.target.value)}
            placeholder={t(mode === "auto" ? "app.admin.dashboard.model.hub.page.outputTokenAuto" : "app.admin.dashboard.model.hub.page.maxTokensPlaceholder")} />
    </div>;
}
