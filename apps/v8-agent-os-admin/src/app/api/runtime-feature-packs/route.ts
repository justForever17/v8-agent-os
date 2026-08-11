import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getRuntimeFeaturePackState, triggerFeaturePackInstall } from "@/lib/server/runtime-feature-packs";
import { verifyServiceAuth } from "@/lib/service-auth";
import { LOCALE_COOKIE_NAME } from "@/lib/locale";

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

function compactLogName(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized ? normalized.split(/[\\/]/).filter(Boolean).pop() || null : null;
}

function publicFeaturePackState(state: Awaited<ReturnType<typeof getRuntimeFeaturePackState>>) {
    return {
        engineAvailable: state.engineAvailable,
        refreshing: state.refreshing,
        retryAfterMs: state.retryAfterMs,
        updatedAt: state.updatedAt,
        summary: state.summary,
        packs: state.packs.map((pack) => ({
            id: pack.id,
            productName: pack.productName,
            shortName: pack.shortName,
            description: pack.description,
            hover: pack.hover,
            recommendedOrder: pack.recommendedOrder,
            runtimeFamilies: pack.runtimeFamilies,
            status: pack.status,
            installed: pack.installed,
            installable: pack.installable,
            restartRequired: pack.restartRequired,
            updatedAt: pack.updatedAt,
            version: pack.version || null,
            executionProvider: pack.executionProvider || null,
            gpuAdapters: pack.gpuAdapters || [],
            logName: compactLogName(pack.logRef),
            hasError: Boolean(pack.lastError),
        })),
    };
}

function publicFeaturePackInstallResult(result: Awaited<ReturnType<typeof triggerFeaturePackInstall>>) {
    const raw = result as Record<string, unknown>;
    const sourceStrategy = Array.isArray(raw.sourceStrategy)
        ? raw.sourceStrategy.map((item) => {
            const source = item && typeof item === "object" ? item as Record<string, unknown> : {};
            return { id: String(source.id || ""), label: String(source.label || "") };
        })
        : [];
    return {
        status: String(raw.status || ""),
        packId: String(raw.packId || ""),
        restartRequired: Boolean(raw.restartRequired),
        message: raw.message ? String(raw.message) : null,
        logName: compactLogName(raw.logRef),
        sourceStrategy,
        assetManifest: raw.assetManifest || null,
    };
}

export async function GET(req: NextRequest) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const state = await getRuntimeFeaturePackState({
            forceHealthRefresh: req.nextUrl.searchParams.get("refresh") === "1",
        });
        return NextResponse.json(publicFeaturePackState(state));
    } catch (error) {
        console.error("[Admin Runtime Feature Packs] Failed to read state:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const payload = await req.json().catch(() => ({}));
        const result = await triggerFeaturePackInstall(
            String(payload?.packId || ""),
            Boolean(payload?.dryRun),
            String(payload?.locale || req.cookies.get(LOCALE_COOKIE_NAME)?.value || "en"),
        );
        return NextResponse.json(publicFeaturePackInstallResult(result));
    } catch (error) {
        console.error("[Admin Runtime Feature Packs] Failed to start install:", error);
        const unavailableErrors = new Set([
            "feature_pack_not_available",
            "feature_pack_lock_unavailable",
            "feature_pack_python_runtime_unavailable",
            "feature_pack_install_busy",
        ]);
        const rawCode = error instanceof Error ? error.message : "";
        const code = rawCode === "feature_pack_install_busy"
            ? rawCode
            : unavailableErrors.has(rawCode)
                ? "feature_pack_not_available"
                : "feature_pack_install_failed";
        return NextResponse.json(
            { error: code },
            { status: code === "feature_pack_not_available" || code === "feature_pack_install_busy" ? 409 : 500 },
        );
    }
}
