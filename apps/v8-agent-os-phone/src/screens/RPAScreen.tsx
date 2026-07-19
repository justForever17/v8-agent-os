import { Redirect, router, type Href } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ActivityIndicator,
    Pressable,
    RefreshControl,
    ScrollView,
    StyleSheet,
    Switch,
    Text,
    TextInput,
    View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { CheckCircle2, ChevronDown, ChevronUp, CirclePlay, Plus, RefreshCw, Trash2, Workflow } from "lucide-react-native";

import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { getRpaAvailability, listRpaTemplates, runRpaTemplate } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { RPAAvailability, RPATemplateSummary } from "@/src/types/admin";

type ExtraField = { id: number; name: string; value: string };
type TemplateVariable = NonNullable<RPATemplateSummary["variables"]>[number] & { source?: string };
type Translator = ReturnType<typeof useUiPrefs>["t"];

const GITHUB_STAR_TEMPLATE_ID = "system.github.star_repository";
const GITHUB_STAR_FIELD_KEYS: Record<string, { label: string; description?: string }> = {
    repo_owner: { label: "src.screens.rpascreen.system.github_star.repo_owner" },
    repo_name: { label: "src.screens.rpascreen.system.github_star.repo_name" },
    repo_url: { label: "src.screens.rpascreen.system.github_star.repo_url" },
    desired_state: {
        label: "src.screens.rpascreen.system.github_star.desired_state",
        description: "src.screens.rpascreen.system.github_star.desired_state_help",
    },
};

function text(value: unknown) {
    return String(value ?? "").trim();
}

function humanizeFieldName(value: string) {
    return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initialValue(variable: TemplateVariable) {
    const explicit = variable.defaultValue ?? variable.default;
    if (explicit !== undefined && explicit !== null) return String(explicit);
    if (variable.source === "template_default" && variable.exampleValue !== undefined && variable.exampleValue !== null) {
        return String(variable.exampleValue);
    }
    return "";
}

function coerceValue(value: string, type: string) {
    if (type === "boolean") return value === "true";
    if (["number", "integer", "float"].includes(type) && value.trim()) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : value;
    }
    return value;
}

function templateName(template: RPATemplateSummary, t: Translator) {
    return text(template.id) === GITHUB_STAR_TEMPLATE_ID
        ? t("src.screens.rpascreen.system.github_star.name")
        : text(template.name) || text(template.id);
}

function templateGoal(template: RPATemplateSummary | null, t: Translator) {
    if (!template) return "";
    return text(template.id) === GITHUB_STAR_TEMPLATE_ID
        ? t("src.screens.rpascreen.system.github_star.goal")
        : text(template.goal);
}

function variableLabel(template: RPATemplateSummary | null, variable: TemplateVariable, t: Translator) {
    const name = text(variable.name);
    const key = text(template?.id) === GITHUB_STAR_TEMPLATE_ID ? GITHUB_STAR_FIELD_KEYS[name]?.label : "";
    return key ? t(key) : text(variable.label) || humanizeFieldName(name);
}

function variableDescription(template: RPATemplateSummary | null, variable: TemplateVariable, t: Translator) {
    const name = text(variable.name);
    const key = text(template?.id) === GITHUB_STAR_TEMPLATE_ID ? GITHUB_STAR_FIELD_KEYS[name]?.description : "";
    return key ? t(key) : text(variable.description);
}

function usesComputerUsePlaybook(template: RPATemplateSummary | null) {
    if (!template) return false;
    if (text(template.robot?.metadata?.executionAdapter) === "computer_use_playbook") return true;
    return (template.steps || []).some((step) => text(step.use) === "computer_use_playbook");
}

export default function RPAScreen() {
    const { status, userAvatarUri, authorizedFetch } = useAppSession();
    const { t, colors, themeMode, toggleThemeMode } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const nextExtraId = useRef(1);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [starting, setStarting] = useState(false);
    const [availability, setAvailability] = useState<RPAAvailability | null>(null);
    const [templates, setTemplates] = useState<RPATemplateSummary[]>([]);
    const [selectedTemplateId, setSelectedTemplateId] = useState("");
    const [templatePickerOpen, setTemplatePickerOpen] = useState(false);
    const [values, setValues] = useState<Record<string, string>>({});
    const [extraFields, setExtraFields] = useState<ExtraField[]>([]);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");

    const actions: PhoneTopbarAction[] = [
        { key: "desktop-live", onPress: () => router.push("/desktop-live" as Href) },
        { key: "theme", onPress: () => void toggleThemeMode() },
    ];
    const selectedTemplate = useMemo(
        () => templates.find((template) => text(template.id) === selectedTemplateId) || null,
        [selectedTemplateId, templates],
    );
    const variables = useMemo(
        () => (selectedTemplate?.variables || []).filter((variable) => text(variable.name)) as TemplateVariable[],
        [selectedTemplate],
    );
    const runtimeReady = Boolean(
        usesComputerUsePlaybook(selectedTemplate)
        || availability?.robotFramework
        || availability?.rpaFramework,
    );

    const load = useCallback(async (manual = false) => {
        if (manual) setRefreshing(true);
        else setLoading(true);
        setError("");
        try {
            const [nextAvailability, nextTemplates] = await Promise.all([
                getRpaAvailability(authorizedFetch),
                listRpaTemplates(authorizedFetch, 100, "approved"),
            ]);
            const usableTemplates = nextTemplates.filter((template) => text(template.id));
            setAvailability(nextAvailability);
            setTemplates(usableTemplates);
            setSelectedTemplateId((current) => usableTemplates.some((template) => text(template.id) === current)
                ? current
                : text(usableTemplates[0]?.id));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t("src.screens.rpascreen.load_failed"));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [authorizedFetch, t]);

    useEffect(() => {
        if (status === "authenticated") void load();
    }, [load, status]);

    useEffect(() => {
        const nextValues: Record<string, string> = {};
        for (const variable of variables) nextValues[text(variable.name)] = initialValue(variable);
        setValues(nextValues);
        setExtraFields([]);
        setError("");
        setNotice("");
    }, [selectedTemplateId, variables]);

    const start = async () => {
        if (!selectedTemplate || !selectedTemplateId || starting) return;
        const missing = variables
            .filter((variable) => variable.required && !text(values[text(variable.name)]))
            .map((variable) => variableLabel(selectedTemplate, variable, t));
        if (missing.length) {
            setError(t("src.screens.rpascreen.required_fields", { fields: missing.join(", ") }));
            return;
        }

        const payloadVariables: Record<string, unknown> = {};
        for (const variable of variables) {
            const name = text(variable.name);
            const value = values[name] ?? "";
            if (!text(value) && text(variable.type).toLowerCase() !== "boolean") continue;
            payloadVariables[name] = coerceValue(value, text(variable.type).toLowerCase());
        }
        const knownNames = new Set(Object.keys(payloadVariables));
        for (const field of extraFields) {
            const name = text(field.name);
            if (!name || !text(field.value)) continue;
            if (knownNames.has(name)) {
                setError(t("src.screens.rpascreen.duplicate_field", { field: name }));
                return;
            }
            knownNames.add(name);
            payloadVariables[name] = field.value;
        }

        setStarting(true);
        setError("");
        setNotice("");
        try {
            const payload = await runRpaTemplate(authorizedFetch, selectedTemplateId, payloadVariables);
            if (["failed", "blocked"].includes(text(payload.status).toLowerCase())) {
                throw new Error(text(payload.error || payload.detail || payload.reason) || t("src.screens.rpascreen.start_failed"));
            }
            setNotice(t("src.screens.rpascreen.started", { template: templateName(selectedTemplate, t) }));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t("src.screens.rpascreen.start_failed"));
        } finally {
            setStarting(false);
        }
    };

    if (status === "booting" || loading) return <LoadingScreen label={t("src.screens.rpascreen.loading")} />;
    if (status === "anonymous") return <Redirect href="/login" />;

    return (
        <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.backgroundDeep }]} edges={["top", "left", "right"]}>
            <PhoneTopbar actions={actions} userImageUri={userAvatarUri || undefined} onBrandPress={() => void goHomeToChat()} />
            <ScrollView
                contentInsetAdjustmentBehavior="automatic"
                refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} tintColor={colors.primary} />}
                contentContainerStyle={styles.content}
                keyboardShouldPersistTaps="handled"
            >
                <View style={styles.hero}>
                    <View style={[styles.heroIcon, { backgroundColor: colors.primarySoft }]}><Workflow size={22} color={colors.primary} /></View>
                    <View style={styles.heroText}>
                        <Text selectable style={[styles.title, { color: colors.text }]}>{t("src.screens.rpascreen.title")}</Text>
                        <Text selectable style={[styles.subtitle, { color: colors.textMuted }]}>{t("src.screens.rpascreen.subtitle")}</Text>
                    </View>
                    <View style={[styles.statusPill, { borderColor: runtimeReady ? `${colors.success}55` : `${colors.warning}55`, backgroundColor: runtimeReady ? `${colors.success}12` : `${colors.warning}12` }]}>
                        <View style={[styles.statusDot, { backgroundColor: runtimeReady ? colors.success : colors.warning }]} />
                        <Text style={[styles.statusText, { color: runtimeReady ? colors.success : colors.warning }]}>{runtimeReady ? t("src.screens.rpascreen.runtime_ready") : t("src.screens.rpascreen.runtime_unavailable")}</Text>
                    </View>
                </View>

                {error ? <View style={[styles.banner, { borderColor: `${colors.danger}55`, backgroundColor: `${colors.danger}10` }]}><Text selectable style={[styles.bannerText, { color: colors.danger }]}>{error}</Text></View> : null}
                {notice ? <View style={[styles.banner, styles.noticeBanner, { borderColor: `${colors.success}55`, backgroundColor: `${colors.success}10` }]}><CheckCircle2 size={17} color={colors.success} /><Text selectable style={[styles.bannerText, { color: colors.success }]}>{notice}</Text></View> : null}

                <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border, boxShadow: themeMode === "dark" ? "0 12px 30px rgba(0,0,0,0.22)" : "0 12px 30px rgba(15,23,42,0.07)" }]}>
                    <Text selectable style={[styles.sectionTitle, { color: colors.text }]}>{t("src.screens.rpascreen.choose_template")}</Text>
                    <Text selectable style={[styles.sectionHint, { color: colors.textMuted }]}>{t("src.screens.rpascreen.choose_template_hint")}</Text>
                    <Pressable
                        onPress={() => setTemplatePickerOpen((current) => !current)}
                        style={({ pressed }) => [styles.selector, { borderColor: colors.border, backgroundColor: colors.surfaceMuted, opacity: pressed ? 0.78 : 1 }]}
                    >
                        <View style={styles.selectorText}>
                            <Text selectable numberOfLines={1} style={[styles.selectorTitle, { color: colors.text }]}>{selectedTemplate ? templateName(selectedTemplate, t) : t("src.screens.rpascreen.no_templates")}</Text>
                            {selectedTemplate ? <Text numberOfLines={1} style={[styles.selectorMeta, { color: colors.textMuted }]}>{t("src.screens.rpascreen.field_count", { count: variables.length })}</Text> : null}
                        </View>
                        {templatePickerOpen ? <ChevronUp size={18} color={colors.textMuted} /> : <ChevronDown size={18} color={colors.textMuted} />}
                    </Pressable>
                    {templatePickerOpen ? (
                        <View style={[styles.templateMenu, { borderColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                            {templates.length ? templates.map((template) => {
                                const id = text(template.id);
                                const active = id === selectedTemplateId;
                                return (
                                    <Pressable key={id} onPress={() => { setSelectedTemplateId(id); setTemplatePickerOpen(false); }} style={({ pressed }) => [styles.templateOption, active && { backgroundColor: colors.primarySoft }, pressed && styles.pressed]}>
                                        <Text numberOfLines={1} style={[styles.templateOptionTitle, { color: colors.text }]}>{templateName(template, t)}</Text>
                                        <Text numberOfLines={1} style={[styles.templateOptionMeta, { color: colors.textMuted }]}>{t("src.screens.rpascreen.approved")}</Text>
                                    </Pressable>
                                );
                            }) : <Text selectable style={[styles.emptyText, { color: colors.textMuted }]}>{t("src.screens.rpascreen.no_templates")}</Text>}
                        </View>
                    ) : null}
                    {templateGoal(selectedTemplate, t) ? <Text selectable style={[styles.goal, { color: colors.textMuted }]}>{templateGoal(selectedTemplate, t)}</Text> : null}
                </View>

                <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border, boxShadow: themeMode === "dark" ? "0 12px 30px rgba(0,0,0,0.22)" : "0 12px 30px rgba(15,23,42,0.07)" }]}>
                    <View style={styles.sectionHeader}>
                        <View style={styles.sectionHeaderText}>
                            <Text selectable style={[styles.sectionTitle, { color: colors.text }]}>{t("src.screens.rpascreen.fill_details")}</Text>
                            <Text selectable style={[styles.sectionHint, { color: colors.textMuted }]}>{t("src.screens.rpascreen.fill_details_hint")}</Text>
                        </View>
                        <Pressable onPress={() => setExtraFields((current) => [...current, { id: nextExtraId.current++, name: "", value: "" }])} disabled={!selectedTemplate} style={({ pressed }) => [styles.addButton, { backgroundColor: colors.primarySoft, opacity: !selectedTemplate ? 0.45 : pressed ? 0.75 : 1 }]}>
                            <Plus size={15} color={colors.primary} /><Text style={[styles.addButtonText, { color: colors.primary }]}>{t("src.screens.rpascreen.add_field")}</Text>
                        </Pressable>
                    </View>

                    <View style={styles.fields}>
                        {variables.map((variable) => {
                            const name = text(variable.name);
                            const type = text(variable.type).toLowerCase();
                            const options = Array.isArray(variable.enum) ? variable.enum.filter(Boolean) : [];
                            const description = variableDescription(selectedTemplate, variable, t);
                            return (
                                <View key={name} style={styles.field}>
                                    <Text selectable style={[styles.fieldLabel, { color: colors.text }]}>{variableLabel(selectedTemplate, variable, t)}{variable.required ? <Text style={{ color: colors.danger }}> *</Text> : null}</Text>
                                    {options.length ? (
                                        <View style={styles.optionRow}>
                                            {options.map((option) => {
                                                const active = values[name] === option;
                                                return <Pressable key={option} onPress={() => setValues((current) => ({ ...current, [name]: option }))} style={({ pressed }) => [styles.optionChip, { borderColor: active ? colors.primary : colors.border, backgroundColor: active ? colors.primarySoft : colors.surfaceMuted, opacity: pressed ? 0.76 : 1 }]}><Text style={[styles.optionText, { color: active ? colors.primary : colors.text }]}>{option}</Text></Pressable>;
                                            })}
                                        </View>
                                    ) : type === "boolean" ? (
                                        <View style={[styles.booleanRow, { borderColor: colors.border, backgroundColor: colors.surfaceMuted }]}>
                                            <Text style={[styles.booleanLabel, { color: colors.textMuted }]}>{t("src.screens.rpascreen.enabled")}</Text>
                                            <Switch value={(values[name] || "false") === "true"} onValueChange={(enabled) => setValues((current) => ({ ...current, [name]: enabled ? "true" : "false" }))} />
                                        </View>
                                    ) : (
                                        <TextInput value={values[name] || ""} onChangeText={(value) => setValues((current) => ({ ...current, [name]: value }))} placeholder={text(variable.exampleValue) || t("src.screens.rpascreen.optional_value")} placeholderTextColor={colors.textSoft} autoCapitalize="none" autoCorrect={false} style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surfaceMuted }]} />
                                    )}
                                    {description ? <Text selectable style={[styles.fieldDescription, { color: colors.textMuted }]}>{description}</Text> : null}
                                </View>
                            );
                        })}
                        {variables.length === 0 && extraFields.length === 0 ? <View style={[styles.emptyBox, { borderColor: colors.border }]}><Text selectable style={[styles.emptyText, { color: colors.textMuted }]}>{t("src.screens.rpascreen.no_fields_needed")}</Text></View> : null}
                    </View>

                    {extraFields.length ? (
                        <View style={[styles.extraFields, { borderTopColor: colors.border }]}>
                            <Text selectable style={[styles.extraTitle, { color: colors.textMuted }]}>{t("src.screens.rpascreen.extra_fields")}</Text>
                            {extraFields.map((field) => (
                                <View key={field.id} style={styles.extraRow}>
                                    <View style={styles.extraInputs}>
                                        <TextInput value={field.name} onChangeText={(value) => setExtraFields((current) => current.map((item) => item.id === field.id ? { ...item, name: value } : item))} placeholder={t("src.screens.rpascreen.field_name")} placeholderTextColor={colors.textSoft} autoCapitalize="none" autoCorrect={false} style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surfaceMuted }]} />
                                        <TextInput value={field.value} onChangeText={(value) => setExtraFields((current) => current.map((item) => item.id === field.id ? { ...item, value } : item))} placeholder={t("src.screens.rpascreen.field_value")} placeholderTextColor={colors.textSoft} style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surfaceMuted }]} />
                                    </View>
                                    <Pressable accessibilityLabel={t("src.screens.rpascreen.remove_field")} onPress={() => setExtraFields((current) => current.filter((item) => item.id !== field.id))} style={({ pressed }) => [styles.removeButton, { backgroundColor: pressed ? `${colors.danger}14` : "transparent" }]}><Trash2 size={17} color={colors.textMuted} /></Pressable>
                                </View>
                            ))}
                        </View>
                    ) : null}

                    <Pressable disabled={!selectedTemplate || starting} onPress={() => void start()} style={({ pressed }) => [styles.startButton, { backgroundColor: colors.primary, opacity: !selectedTemplate || starting ? 0.45 : pressed ? 0.82 : 1 }]}>
                        {starting ? <ActivityIndicator color="#FFFFFF" /> : <CirclePlay size={18} color="#FFFFFF" />}
                        <Text style={styles.startButtonText}>{starting ? t("src.screens.rpascreen.starting") : t("src.screens.rpascreen.start")}</Text>
                    </Pressable>
                </View>

                <Pressable onPress={() => void load(true)} style={({ pressed }) => [styles.refreshLink, pressed && styles.pressed]}>
                    <RefreshCw size={14} color={colors.textMuted} /><Text style={[styles.refreshText, { color: colors.textMuted }]}>{t("src.screens.rpascreen.refresh")}</Text>
                </Pressable>
            </ScrollView>
        </SafeAreaView>
    );
}

const styles = StyleSheet.create({
    safeArea: { flex: 1 },
    content: { padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md },
    hero: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: spacing.xs },
    heroIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", borderCurve: "continuous" },
    heroText: { flex: 1, gap: 3 },
    title: { fontSize: 22, lineHeight: 27, fontWeight: "900", letterSpacing: -0.5 },
    subtitle: { fontSize: 12, lineHeight: 18 },
    statusPill: { flexDirection: "row", alignItems: "center", gap: 5, borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.pill, paddingHorizontal: 9, paddingVertical: 6 },
    statusDot: { width: 6, height: 6, borderRadius: 3 },
    statusText: { fontSize: 10, fontWeight: "800" },
    banner: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.sm, padding: 12, borderCurve: "continuous" },
    noticeBanner: { flexDirection: "row", alignItems: "center", gap: 8 },
    bannerText: { flex: 1, fontSize: 12, lineHeight: 18, fontWeight: "600" },
    card: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.md, padding: spacing.md, borderCurve: "continuous", gap: spacing.sm },
    sectionHeader: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm },
    sectionHeaderText: { flex: 1, gap: 3 },
    sectionTitle: { fontSize: 15, lineHeight: 20, fontWeight: "800" },
    sectionHint: { fontSize: 11, lineHeight: 17 },
    selector: { minHeight: 50, borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.sm, paddingHorizontal: 12, paddingVertical: 9, flexDirection: "row", alignItems: "center", gap: 8, borderCurve: "continuous" },
    selectorText: { flex: 1, gap: 2 },
    selectorTitle: { fontSize: 13, fontWeight: "800" },
    selectorMeta: { fontSize: 10 },
    templateMenu: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.sm, padding: 4, maxHeight: 220, borderCurve: "continuous" },
    templateOption: { paddingHorizontal: 10, paddingVertical: 9, borderRadius: 10, gap: 2 },
    templateOptionTitle: { fontSize: 12, fontWeight: "700" },
    templateOptionMeta: { fontSize: 10 },
    goal: { fontSize: 11, lineHeight: 18 },
    addButton: { height: 32, borderRadius: 10, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 4 },
    addButtonText: { fontSize: 11, fontWeight: "800" },
    fields: { gap: spacing.md },
    field: { gap: 6 },
    fieldLabel: { fontSize: 12, fontWeight: "800" },
    input: { minHeight: 42, borderWidth: StyleSheet.hairlineWidth, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 9, fontSize: 13, borderCurve: "continuous" },
    fieldDescription: { fontSize: 10, lineHeight: 16 },
    optionRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
    optionChip: { minHeight: 36, borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.pill, paddingHorizontal: 12, alignItems: "center", justifyContent: "center" },
    optionText: { fontSize: 11, fontWeight: "700" },
    booleanRow: { minHeight: 44, borderWidth: StyleSheet.hairlineWidth, borderRadius: 12, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderCurve: "continuous" },
    booleanLabel: { fontSize: 12, fontWeight: "600" },
    emptyBox: { borderWidth: StyleSheet.hairlineWidth, borderStyle: "dashed", borderRadius: radii.sm, padding: spacing.lg, alignItems: "center", borderCurve: "continuous" },
    emptyText: { fontSize: 11, lineHeight: 17, textAlign: "center" },
    extraFields: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: spacing.md, gap: spacing.sm },
    extraTitle: { fontSize: 11, fontWeight: "800" },
    extraRow: { flexDirection: "row", alignItems: "center", gap: 8 },
    extraInputs: { flex: 1, gap: 8 },
    removeButton: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center" },
    startButton: { minHeight: 46, borderRadius: 14, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderCurve: "continuous" },
    startButtonText: { color: "#FFFFFF", fontSize: 13, fontWeight: "900" },
    refreshLink: { alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 6, padding: 8 },
    refreshText: { fontSize: 11, fontWeight: "600" },
    pressed: { opacity: 0.72 },
});
