"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
    ArrowLeft,
    CheckCircle2,
    ChevronDown,
    CirclePlay,
    Plus,
    RefreshCw,
    Trash2,
    Workflow,
} from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type TemplateVariable = {
    name?: string;
    label?: string;
    description?: string;
    type?: string;
    required?: boolean;
    enum?: string[];
    source?: string;
    exampleValue?: unknown;
    defaultValue?: unknown;
    default?: unknown;
};

type RpaTemplate = {
    id?: string;
    name?: string;
    goal?: string;
    status?: string;
    variables?: TemplateVariable[];
    steps?: Array<{ use?: string }>;
    robot?: { metadata?: { executionAdapter?: string } };
    view?: { statusLabel?: string };
};

type AvailabilityPayload = {
    robotFramework?: boolean;
    rpaFramework?: boolean;
};

type ExtraField = { id: number; name: string; value: string };
type Translator = ReturnType<typeof useT>;

const GITHUB_STAR_TEMPLATE_ID = "system.github.star_repository";
const GITHUB_STAR_FIELD_KEYS: Record<string, { label: string; description?: string }> = {
    repo_owner: { label: "web.rpa.system.githubStar.repoOwner" },
    repo_name: { label: "web.rpa.system.githubStar.repoName" },
    repo_url: { label: "web.rpa.system.githubStar.repoUrl" },
    desired_state: {
        label: "web.rpa.system.githubStar.desiredState",
        description: "web.rpa.system.githubStar.desiredStateHelp",
    },
};

function text(value: unknown) {
    return String(value ?? "").trim();
}

function humanizeFieldName(value: string) {
    return value
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initialValue(variable: TemplateVariable) {
    const explicit = variable.defaultValue ?? variable.default;
    if (explicit !== undefined && explicit !== null) return String(explicit);
    if (variable.source === "template_default" && variable.exampleValue !== undefined && variable.exampleValue !== null) {
        return String(variable.exampleValue);
    }
    return "";
}

function normalizedVariables(template: RpaTemplate | null) {
    return (template?.variables || []).filter((variable) => text(variable.name));
}

function templateName(template: RpaTemplate, t: Translator) {
    return text(template.id) === GITHUB_STAR_TEMPLATE_ID
        ? t("web.rpa.system.githubStar.name")
        : text(template.name) || text(template.id);
}

function templateGoal(template: RpaTemplate | null, t: Translator) {
    if (!template) return "";
    return text(template.id) === GITHUB_STAR_TEMPLATE_ID
        ? t("web.rpa.system.githubStar.goal")
        : text(template.goal);
}

function variableLabel(template: RpaTemplate | null, variable: TemplateVariable, t: Translator) {
    const name = text(variable.name);
    const key = text(template?.id) === GITHUB_STAR_TEMPLATE_ID ? GITHUB_STAR_FIELD_KEYS[name]?.label : "";
    return key ? t(key) : text(variable.label) || humanizeFieldName(name);
}

function variableDescription(template: RpaTemplate | null, variable: TemplateVariable, t: Translator) {
    const name = text(variable.name);
    const key = text(template?.id) === GITHUB_STAR_TEMPLATE_ID ? GITHUB_STAR_FIELD_KEYS[name]?.description : "";
    return key ? t(key) : text(variable.description);
}

function usesComputerUsePlaybook(template: RpaTemplate | null) {
    if (!template) return false;
    if (text(template.robot?.metadata?.executionAdapter) === "computer_use_playbook") return true;
    return (template.steps || []).some((step) => text(step.use) === "computer_use_playbook");
}

function coerceValue(value: string, type: string) {
    if (type === "boolean") return value === "true";
    if (["number", "integer", "float"].includes(type) && value.trim()) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : value;
    }
    return value;
}

export function RPAQuickPanel() {
    const t = useT();
    const nextExtraId = useRef(1);
    const [loading, setLoading] = useState(true);
    const [starting, setStarting] = useState(false);
    const [availability, setAvailability] = useState<AvailabilityPayload>({});
    const [templates, setTemplates] = useState<RpaTemplate[]>([]);
    const [selectedTemplateId, setSelectedTemplateId] = useState("");
    const [values, setValues] = useState<Record<string, string>>({});
    const [extraFields, setExtraFields] = useState<ExtraField[]>([]);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");

    const selectedTemplate = useMemo(
        () => templates.find((template) => text(template.id) === selectedTemplateId) || null,
        [selectedTemplateId, templates],
    );
    const variableDefinitions = useMemo(() => normalizedVariables(selectedTemplate), [selectedTemplate]);
    const runtimeReady = Boolean(
        usesComputerUsePlaybook(selectedTemplate)
        || availability.robotFramework
        || availability.rpaFramework,
    );

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [availabilityResponse, templatesResponse] = await Promise.all([
                fetch("/api/rpa/availability", { cache: "no-store" }),
                fetch("/api/rpa/templates?status=approved&limit=100", { cache: "no-store" }),
            ]);
            const availabilityPayload = await availabilityResponse.json().catch(() => ({}));
            const templatePayload = await templatesResponse.json().catch(() => ({}));
            if (!templatesResponse.ok) {
                throw new Error(text(templatePayload?.detail || templatePayload?.error) || t("web.rpa.loadFailed"));
            }
            const nextTemplates = Array.isArray(templatePayload?.templates)
                ? templatePayload.templates.filter((template: RpaTemplate) => text(template.id))
                : [];
            setAvailability(availabilityResponse.ok ? availabilityPayload : {});
            setTemplates(nextTemplates);
            setSelectedTemplateId((current) => nextTemplates.some((item: RpaTemplate) => text(item.id) === current)
                ? current
                : text(nextTemplates[0]?.id));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t("web.rpa.loadFailed"));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        void load();
    }, [load]);

    useEffect(() => {
        const nextValues: Record<string, string> = {};
        for (const variable of normalizedVariables(selectedTemplate)) {
            nextValues[text(variable.name)] = initialValue(variable);
        }
        setValues(nextValues);
        setExtraFields([]);
        setError("");
        setNotice("");
    }, [selectedTemplate]);

    const addExtraField = () => {
        const id = nextExtraId.current++;
        setExtraFields((current) => [...current, { id, name: "", value: "" }]);
    };

    const startTemplate = async () => {
        if (!selectedTemplateId || !selectedTemplate) return;
        const missing = variableDefinitions
            .filter((variable) => variable.required && !text(values[text(variable.name)]))
            .map((variable) => variableLabel(selectedTemplate, variable, t));
        if (missing.length) {
            setError(t("web.rpa.requiredFields", { fields: missing.join(", ") }));
            return;
        }

        const payloadVariables: Record<string, unknown> = {};
        for (const variable of variableDefinitions) {
            const name = text(variable.name);
            const value = values[name] ?? "";
            if (!text(value) && variable.type !== "boolean") continue;
            payloadVariables[name] = coerceValue(value, text(variable.type).toLowerCase());
        }
        const knownNames = new Set(Object.keys(payloadVariables));
        for (const field of extraFields) {
            const name = text(field.name);
            if (!name || !text(field.value)) continue;
            if (knownNames.has(name)) {
                setError(t("web.rpa.duplicateField", { field: name }));
                return;
            }
            knownNames.add(name);
            payloadVariables[name] = field.value;
        }

        setStarting(true);
        setError("");
        setNotice("");
        try {
            const response = await fetch(`/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/run`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ variables: payloadVariables, triggerSource: "rpa_web", nonChatRun: true }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || ["failed", "blocked"].includes(text(payload?.status).toLowerCase())) {
                throw new Error(text(payload?.detail || payload?.error || payload?.reason) || t("web.rpa.startFailed"));
            }
            setNotice(t("web.rpa.started", { template: templateName(selectedTemplate, t) }));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t("web.rpa.startFailed"));
        } finally {
            setStarting(false);
        }
    };

    return (
        <div className="h-full min-h-0 overflow-auto bg-[radial-gradient(circle_at_50%_-20%,hsl(var(--primary)/0.10),transparent_42%)]">
            <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-5 sm:px-6 sm:py-7">
                <header className="flex flex-col gap-4 border-b border-border/65 pb-5 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <Button variant="ghost" size="sm" asChild className="-ml-3 mb-2 text-muted-foreground hover:text-foreground">
                            <Link href="/chat"><ArrowLeft className="mr-2 h-4 w-4" />{t("web.rpa.back")}</Link>
                        </Button>
                        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
                            <Workflow className="h-4 w-4" />RPA
                        </div>
                        <h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">{t("web.rpa.title")}</h1>
                        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{t("web.rpa.subtitle")}</p>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs ${runtimeReady ? "border-emerald-500/25 bg-emerald-500/8 text-emerald-700 dark:text-emerald-300" : "border-amber-500/25 bg-amber-500/8 text-amber-700 dark:text-amber-300"}`}>
                            <span className={`h-1.5 w-1.5 rounded-full ${runtimeReady ? "bg-emerald-500" : "bg-amber-500"}`} />
                            {runtimeReady ? t("web.rpa.runtimeReady") : t("web.rpa.runtimeUnavailable")}
                        </span>
                        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading || starting}>
                            <RefreshCw className={`mr-2 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />{t("web.rpa.refresh")}
                        </Button>
                    </div>
                </header>

                {error ? <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive">{error}</div> : null}
                {notice ? <div className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300"><CheckCircle2 className="h-4 w-4" />{notice}</div> : null}

                <section className="grid gap-5 lg:grid-cols-[minmax(0,0.86fr)_minmax(0,1.4fr)]">
                    <div className="rounded-2xl border border-border/65 bg-background/82 p-4 shadow-sm backdrop-blur-sm sm:p-5">
                        <div className="text-sm font-semibold">{t("web.rpa.chooseTemplate")}</div>
                        <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("web.rpa.chooseTemplateHint")}</p>
                        <div className="relative mt-4">
                            <select
                                value={selectedTemplateId}
                                onChange={(event) => setSelectedTemplateId(event.target.value)}
                                disabled={loading || templates.length === 0}
                                className="h-11 w-full appearance-none rounded-xl border border-input bg-background px-3 pr-10 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-50"
                            >
                                {templates.length === 0 ? <option value="">{loading ? t("web.rpa.loading") : t("web.rpa.noTemplates")}</option> : null}
                                {templates.map((template) => <option key={text(template.id)} value={text(template.id)}>{templateName(template, t)}</option>)}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-3 top-3.5 h-4 w-4 text-muted-foreground" />
                        </div>
                        {templateGoal(selectedTemplate, t) ? <p className="mt-4 line-clamp-5 text-xs leading-5 text-muted-foreground">{templateGoal(selectedTemplate, t)}</p> : null}
                        {selectedTemplate ? (
                            <div className="mt-4 flex items-center gap-2 text-[11px] text-muted-foreground">
                                <span className="rounded-full border border-border/65 bg-muted/30 px-2 py-1">{t("web.rpa.approved")}</span>
                                <span>{t("web.rpa.fieldCount", { count: variableDefinitions.length })}</span>
                            </div>
                        ) : null}
                    </div>

                    <div className="rounded-2xl border border-border/65 bg-background/88 p-4 shadow-sm backdrop-blur-sm sm:p-5">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h2 className="text-sm font-semibold">{t("web.rpa.fillDetails")}</h2>
                                <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("web.rpa.fillDetailsHint")}</p>
                            </div>
                            <Button type="button" variant="ghost" size="sm" onClick={addExtraField} disabled={!selectedTemplate}>
                                <Plus className="mr-1.5 h-3.5 w-3.5" />{t("web.rpa.addField")}
                            </Button>
                        </div>

                        <div className="mt-5 grid gap-4 sm:grid-cols-2">
                            {variableDefinitions.map((variable) => {
                                const name = text(variable.name);
                                const label = variableLabel(selectedTemplate, variable, t);
                                const description = variableDescription(selectedTemplate, variable, t);
                                const type = text(variable.type).toLowerCase();
                                const options = Array.isArray(variable.enum) ? variable.enum.filter(Boolean) : [];
                                return (
                                    <div key={name} className={description ? "sm:col-span-2" : ""}>
                                        <Label htmlFor={`rpa-field-${name}`} className="text-xs">
                                            {label}{variable.required ? <span className="ml-1 text-destructive">*</span> : null}
                                        </Label>
                                        {options.length ? (
                                            <div className="relative mt-1.5">
                                                <select id={`rpa-field-${name}`} value={values[name] || ""} onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))} className="h-10 w-full appearance-none rounded-xl border border-input bg-background px-3 pr-9 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15">
                                                    <option value="">{t("web.rpa.selectValue")}</option>
                                                    {options.map((option) => <option key={option} value={option}>{option}</option>)}
                                                </select>
                                                <ChevronDown className="pointer-events-none absolute right-3 top-3 h-4 w-4 text-muted-foreground" />
                                            </div>
                                        ) : type === "boolean" ? (
                                            <label className="mt-1.5 flex h-10 items-center gap-2 rounded-xl border border-input px-3 text-sm">
                                                <input type="checkbox" checked={(values[name] || "false") === "true"} onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.checked ? "true" : "false" }))} />
                                                {t("web.rpa.enabled")}
                                            </label>
                                        ) : (
                                            <Input id={`rpa-field-${name}`} value={values[name] || ""} onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))} placeholder={text(variable.exampleValue) || t("web.rpa.optionalValue")} className="mt-1.5 rounded-xl" />
                                        )}
                                        {description ? <p className="mt-1.5 text-[11px] leading-5 text-muted-foreground">{description}</p> : null}
                                    </div>
                                );
                            })}
                        </div>

                        {variableDefinitions.length === 0 && extraFields.length === 0 ? <div className="mt-5 rounded-xl border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">{t("web.rpa.noFieldsNeeded")}</div> : null}

                        {extraFields.length ? (
                            <div className="mt-5 space-y-3 border-t border-border/55 pt-5">
                                <div className="text-xs font-semibold text-muted-foreground">{t("web.rpa.extraFields")}</div>
                                {extraFields.map((field) => (
                                    <div key={field.id} className="grid gap-2 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)_auto]">
                                        <Input value={field.name} onChange={(event) => setExtraFields((current) => current.map((item) => item.id === field.id ? { ...item, name: event.target.value } : item))} placeholder={t("web.rpa.fieldName")} className="rounded-xl" />
                                        <Input value={field.value} onChange={(event) => setExtraFields((current) => current.map((item) => item.id === field.id ? { ...item, value: event.target.value } : item))} placeholder={t("web.rpa.fieldValue")} className="rounded-xl" />
                                        <Button type="button" variant="ghost" size="icon" onClick={() => setExtraFields((current) => current.filter((item) => item.id !== field.id))} aria-label={t("web.rpa.removeField")}><Trash2 className="h-4 w-4" /></Button>
                                    </div>
                                ))}
                            </div>
                        ) : null}

                        <Button type="button" onClick={() => void startTemplate()} disabled={!selectedTemplate || starting || loading} className="mt-6 h-11 w-full rounded-xl text-sm font-semibold">
                            {starting ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <CirclePlay className="mr-2 h-4 w-4" />}
                            {starting ? t("web.rpa.starting") : t("web.rpa.start")}
                        </Button>
                    </div>
                </section>
            </div>
        </div>
    );
}
