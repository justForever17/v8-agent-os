import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { constants as fsConstants } from "node:fs";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveConfigDomain } from "@/lib/server/runtime-config";

const WORKSPACE_RULES_BUDGET_TOKENS = 10_000;
const DEFAULT_AGENTS_TEMPLATE = [
    "# Workspace Rules",
    "",
    "Add concise runtime instructions for this workspace here.",
    "Keep this file under 10000 estimated tokens.",
    "",
].join("\n");

function resolveDefaultWorkspacePath() {
    const workspaceConfig = resolveConfigDomain<Record<string, unknown>>("workspace", {});
    const configured = String(workspaceConfig.agent_workspace_path || "").trim();
    return configured || path.join(os.homedir(), ".v8-agent-os", "workspace");
}

function expandHomeDirectory(value: string) {
    if (value === "~") {
        return os.homedir();
    }
    if (value.startsWith("~/") || value.startsWith("~\\")) {
        return path.join(os.homedir(), value.slice(2));
    }
    return value;
}

function isAbsolutePath(value: string) {
    const normalized = String(value || "").trim();
    return /^[a-zA-Z]:[\\/]/.test(normalized) || normalized.startsWith("/") || normalized.startsWith("\\\\");
}

function normalizeWorkspacePath(value?: string | null) {
    const normalized = expandHomeDirectory(String(value || "").trim());
    return normalized ? path.resolve(normalized) : "";
}

function resolveWorkspacePath(req: NextRequest, body?: Record<string, unknown>) {
    const fromQuery = req.nextUrl.searchParams.get("workspacePath");
    const fromBody = typeof body?.workspacePath === "string" ? body.workspacePath : "";
    const candidate = String(fromQuery || fromBody || "").trim();
    if (!candidate) {
        return resolveDefaultWorkspacePath();
    }
    return normalizeWorkspacePath(candidate);
}

function agentsPathForWorkspace(workspacePath: string) {
    return path.join(workspacePath, ".agents", "rules", "AGENTS.md");
}

async function ensureWorkspaceSkeleton(workspacePath: string) {
    const agentsRoot = path.join(workspacePath, ".agents");
    const rulesRoot = path.join(agentsRoot, "rules");
    const skillsRoot = path.join(agentsRoot, "skills");
    const agentsFile = agentsPathForWorkspace(workspacePath);
    await fs.mkdir(rulesRoot, { recursive: true });
    await fs.mkdir(skillsRoot, { recursive: true });
    const exists = await fs.stat(agentsFile).then((stat) => stat.isFile()).catch(() => false);
    if (!exists) {
        await fs.writeFile(agentsFile, DEFAULT_AGENTS_TEMPLATE, "utf-8");
    }
}

function estimatePromptTokens(text: string) {
    const raw = String(text || "");
    if (!raw) {
        return 0;
    }
    let cjkCount = 0;
    let nonCjkVisible = 0;
    for (const char of raw) {
        const codepoint = char.codePointAt(0) || 0;
        if (
            (codepoint >= 0x4e00 && codepoint <= 0x9fff)
            || (codepoint >= 0x3400 && codepoint <= 0x4dbf)
            || (codepoint >= 0x3040 && codepoint <= 0x30ff)
            || (codepoint >= 0xac00 && codepoint <= 0xd7af)
        ) {
            cjkCount += 1;
        } else if (!/\s/.test(char)) {
            nonCjkVisible += 1;
        }
    }
    return cjkCount + Math.ceil(nonCjkVisible / 4);
}

function buildBudgetDiagnostics(content: string) {
    const estimatedTokens = estimatePromptTokens(content);
    return {
        estimatedTokens,
        budgetTokens: WORKSPACE_RULES_BUDGET_TOKENS,
        truncated: estimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS,
        saveRejected: false,
        omittedReason: estimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS ? "workspace_agents_md_runtime_truncated" : "",
    };
}

async function canWriteTarget(targetPath: string) {
    let cursor = targetPath;
    while (cursor) {
        try {
            await fs.access(cursor, fsConstants.W_OK);
            return true;
        } catch {
            const parent = path.dirname(cursor);
            if (!parent || parent === cursor) {
                return false;
            }
            cursor = parent;
        }
    }
    return false;
}

async function buildWorkspaceStatus(workspacePath: string) {
    const normalized = normalizeWorkspacePath(workspacePath);
    const exists = await fs.stat(normalized).then((stat) => stat.isDirectory()).catch(() => false);
    const writable = await canWriteTarget(exists ? normalized : path.dirname(normalized));
    return {
        exists,
        isAbsolute: isAbsolutePath(normalized),
        writable,
        reason: exists ? "" : "workspace_directory_missing",
    };
}

async function readWorkspaceRules(workspacePath: string) {
    const normalizedWorkspacePath = normalizeWorkspacePath(workspacePath);
    const canonicalPath = agentsPathForWorkspace(normalizedWorkspacePath);
    const exists = await fs.stat(canonicalPath).then((stat) => stat.isFile()).catch(() => false);
    const content = exists ? await fs.readFile(canonicalPath, "utf-8") : "";
    return {
        workspacePath: normalizedWorkspacePath,
        path: canonicalPath,
        exists,
        content,
        suggestedContent: DEFAULT_AGENTS_TEMPLATE,
        workspaceStatus: await buildWorkspaceStatus(normalizedWorkspacePath),
        budgetDiagnostics: buildBudgetDiagnostics(content),
    };
}

function validateWorkspacePath(workspacePath: string) {
    if (!workspacePath) {
        return "Workspace path is required.";
    }
    if (!isAbsolutePath(workspacePath)) {
        return "Workspace path must be absolute.";
    }
    return "";
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const workspacePath = resolveWorkspacePath(req);
    const validationError = validateWorkspacePath(workspacePath);
    if (validationError) {
        return NextResponse.json({ error: validationError }, { status: 400 });
    }
    return NextResponse.json(await readWorkspaceRules(workspacePath));
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const body = await req.json().catch(() => ({}));
    const workspacePath = resolveWorkspacePath(req, body);
    const validationError = validateWorkspacePath(workspacePath);
    if (validationError) {
        return NextResponse.json({ error: validationError }, { status: 400 });
    }

    if (body?.ensureOnly === true) {
        await ensureWorkspaceSkeleton(workspacePath);
        return NextResponse.json(await readWorkspaceRules(workspacePath));
    }

    const content = typeof body?.content === "string" ? body.content : DEFAULT_AGENTS_TEMPLATE;
    const budgetDiagnostics = buildBudgetDiagnostics(content);
    if (budgetDiagnostics.estimatedTokens > WORKSPACE_RULES_BUDGET_TOKENS) {
        return NextResponse.json(
            {
                error: `AGENTS.md exceeds ${WORKSPACE_RULES_BUDGET_TOKENS} estimated tokens (${budgetDiagnostics.estimatedTokens}).`,
                budgetDiagnostics: {
                    ...budgetDiagnostics,
                    saveRejected: true,
                    omittedReason: "workspace_agents_md_budget_exceeded",
                },
            },
            { status: 400 },
        );
    }

    const targetPath = agentsPathForWorkspace(workspacePath);
    await ensureWorkspaceSkeleton(workspacePath);
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    await fs.writeFile(targetPath, content, "utf-8");
    return NextResponse.json(await readWorkspaceRules(workspacePath));
}
