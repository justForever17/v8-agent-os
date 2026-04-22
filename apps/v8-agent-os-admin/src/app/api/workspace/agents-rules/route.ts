import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveConfigDomain } from "@/lib/server/runtime-config";

function resolveMainWorkspacePath() {
    const workspaceConfig = resolveConfigDomain<Record<string, unknown>>("workspace", {});
    const configured = String(workspaceConfig.agent_workspace_path || "").trim();
    return configured || path.join(os.homedir(), ".v8-agent-os", "workspace");
}

function agentsPathForMainWorkspace() {
    return path.join(resolveMainWorkspacePath(), ".agents", "rules", "AGENTS.md");
}

async function openPath(targetPath: string) {
    if (process.platform === "win32") {
        spawn("powershell.exe", ["-NoProfile", "-Command", "Start-Process -LiteralPath $args[0]", targetPath], {
            detached: true,
            stdio: "ignore",
        }).unref();
        return;
    }
    if (process.platform === "darwin") {
        spawn("open", [targetPath], { detached: true, stdio: "ignore" }).unref();
        return;
    }
    spawn("xdg-open", [targetPath], { detached: true, stdio: "ignore" }).unref();
}

export async function GET() {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const target = agentsPathForMainWorkspace();
    const exists = await fs.stat(target).then((stat) => stat.isFile()).catch(() => false);
    return NextResponse.json({ path: target, exists });
}

export async function POST(req: Request) {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const body = await req.json().catch(() => ({}));
    const action = String(body?.action || "create").trim();
    const target = agentsPathForMainWorkspace();
    await fs.mkdir(path.dirname(target), { recursive: true });
    const exists = await fs.stat(target).then((stat) => stat.isFile()).catch(() => false);
    if (!exists) {
        await fs.writeFile(
            target,
            "# Workspace Rules\n\nAdd concise runtime instructions for this main workspace here. Keep this file under 10000 estimated tokens.\n",
            "utf-8",
        );
    }
    if (action === "open") {
        await openPath(target);
    }
    return NextResponse.json({ path: target, exists: true, opened: action === "open" });
}
