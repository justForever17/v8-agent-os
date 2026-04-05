export type CanonicalSourceGroup = "web" | "channels" | "cron" | "hooks";

export type CanonicalChannelContext = {
    channelType?: string;
    channelName?: string;
    channelDomain?: string;
    chatType?: string;
    accountId?: string;
    defaultAccount?: string;
};

function parseMetadata(metadata: unknown): Record<string, unknown> {
    if (!metadata) return {};
    if (typeof metadata === "string") {
        try {
            return JSON.parse(metadata) as Record<string, unknown>;
        } catch {
            return {};
        }
    }
    if (typeof metadata === "object") {
        return metadata as Record<string, unknown>;
    }
    return {};
}

function readString(record: Record<string, unknown>, keys: string[]) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return "";
}

function coerceString(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function normalizeSourceGroup(value: unknown): CanonicalSourceGroup | "" {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "";
    if (normalized === "cron") return "cron";
    if (normalized === "hooks") return "hooks";
    if (normalized === "channels") return "channels";
    if (normalized === "web") return "web";
    return "";
}

function normalizeSource(value: unknown) {
    return String(value || "").trim().toLowerCase();
}

function normalizeChatType(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return undefined;
    return normalized === "group" ? "group" : "p2p";
}

export function deriveCanonicalChannelContext(record: Record<string, unknown>): CanonicalChannelContext {
    const summary = record.summary && typeof record.summary === "object"
        ? record.summary as Record<string, unknown>
        : {};
    const metadata = parseMetadata(record.metadata || summary.metadata);
    const source = normalizeSource(
        record.source
        || summary.source
        || record.trigger_source
        || summary.trigger_source
        || metadata.trigger_source
        || metadata.triggerSource
        || metadata.source,
    );
    const handoffSource = normalizeSource(metadata.handoff_source || metadata.handoffSource);
    const transportManagedBy = normalizeSource(metadata.transport_managed_by || metadata.transportManagedBy);
    const bridgeBackedSource = (handoffSource === "openclaw_bridge" || transportManagedBy === "openclaw_bridge")
        && source
        && source !== "web"
        && source !== "cron"
        && source !== "hooks"
        && !source.startsWith("hook")
        && !source.startsWith("trigger")
        && !source.startsWith("cron");

    const channelType = coerceString(
        readString(record, ["channel_type", "channelType"])
        || readString(summary, ["channel_type", "channelType"])
        || readString(metadata, ["channel_type", "channelType"])
        || (bridgeBackedSource ? source : ""),
    );
    const channelName = coerceString(
        readString(record, ["channel_name", "channelName"])
        || readString(summary, ["channel_name", "channelName"])
        || readString(metadata, ["channel_name", "channelName"])
        || channelType,
    );
    const channelDomain = coerceString(
        readString(record, ["channel_domain", "channelDomain"])
        || readString(summary, ["channel_domain", "channelDomain"])
        || readString(metadata, ["channel_domain", "channelDomain"]),
    );
    const chatType = normalizeChatType(
        readString(record, ["chat_type", "chatType"])
        || readString(summary, ["chat_type", "chatType"])
        || readString(metadata, ["chat_type", "chatType"]),
    );
    const accountId = coerceString(
        readString(record, ["account_id", "accountId"])
        || readString(summary, ["account_id", "accountId"])
        || readString(metadata, ["account_id", "accountId"]),
    );
    const defaultAccount = coerceString(
        readString(record, ["default_account", "defaultAccount"])
        || readString(summary, ["default_account", "defaultAccount"])
        || readString(metadata, ["default_account", "defaultAccount"]),
    );

    return {
        ...(channelType ? { channelType } : {}),
        ...(channelName ? { channelName } : {}),
        ...(channelDomain ? { channelDomain } : {}),
        ...(chatType ? { chatType } : {}),
        ...(accountId ? { accountId } : {}),
        ...(defaultAccount ? { defaultAccount } : {}),
    };
}

function hasChannelContext(context: CanonicalChannelContext) {
    return Boolean(
        context.channelType
        || context.channelName
        || context.channelDomain
        || context.accountId
        || context.defaultAccount,
    );
}

function isBridgeManagedChannelRecord(
    source: string,
    metadata: Record<string, unknown>,
    context: CanonicalChannelContext,
) {
    const handoffSource = normalizeSource(metadata.handoff_source || metadata.handoffSource);
    const transportManagedBy = normalizeSource(metadata.transport_managed_by || metadata.transportManagedBy);
    if ((source === "channels" || source === "openclaw_channels" || source === "openclaw_channel") && hasChannelContext(context)) {
        return true;
    }
    if ((handoffSource === "openclaw_bridge" || transportManagedBy === "openclaw_bridge") && hasChannelContext(context)) {
        return true;
    }
    return false;
}

export function deriveCanonicalSourceGroup(record: Record<string, unknown>): CanonicalSourceGroup {
    const summary = record.summary && typeof record.summary === "object"
        ? record.summary as Record<string, unknown>
        : {};
    const metadata = parseMetadata(record.metadata || summary.metadata);
    const channelContext = deriveCanonicalChannelContext(record);

    const explicit = normalizeSourceGroup(
        record.sourceGroup
        || record.source_group
        || summary.sourceGroup
        || summary.source_group,
    );
    if (explicit === "cron" || explicit === "hooks") {
        return explicit;
    }

    const source = normalizeSource(
        record.source
        || summary.source
        || record.trigger_source
        || summary.trigger_source
        || metadata.trigger_source
        || metadata.triggerSource
        || metadata.source,
    );

    if (!source) {
        return "web";
    }
    if (source === "cron" || source.startsWith("cron")) {
        return "cron";
    }
    if (source === "hooks" || source.startsWith("hook") || source.startsWith("trigger")) {
        return "hooks";
    }
    const isCanonicalChannelRecord = isBridgeManagedChannelRecord(source, metadata, channelContext);
    if (explicit === "channels") {
        return isCanonicalChannelRecord ? "channels" : "web";
    }
    if (isCanonicalChannelRecord) {
        return "channels";
    }
    if (explicit === "web") {
        return "web";
    }

    return "web";
}

export function applyCanonicalSourceGroup<T extends Record<string, unknown>>(payload: T): T {
    const canonical = deriveCanonicalSourceGroup(payload);
    const channelContext = deriveCanonicalChannelContext(payload);
    const nextSummary = payload.summary && typeof payload.summary === "object"
        ? {
            ...(payload.summary as Record<string, unknown>),
            sourceGroup: canonical,
            ...channelContext,
        }
        : payload.summary;

    return {
        ...payload,
        sourceGroup: canonical,
        ...channelContext,
        ...(nextSummary ? { summary: nextSummary } : {}),
    };
}
