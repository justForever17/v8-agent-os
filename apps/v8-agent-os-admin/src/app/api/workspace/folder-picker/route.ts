import { NextRequest, NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";

const execFileAsync = promisify(execFile);

function normalizeSelectedPath(value: string) {
    return String(value || "").trim().replace(/[\\\/]+$/, "");
}

async function pickFolderWindows(initialPath: string) {
    const script = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
        "$initial = $args[0]",
        "if ($initial -and (Test-Path -LiteralPath $initial)) { $dialog.SelectedPath = $initial }",
        "$result = $dialog.ShowDialog()",
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {",
        "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "  Write-Output $dialog.SelectedPath",
        "}",
    ].join("; ");
    const { stdout } = await execFileAsync(
        "powershell.exe",
        ["-NoProfile", "-STA", "-Command", script, initialPath || ""],
        { windowsHide: true },
    );
    return normalizeSelectedPath(stdout);
}

async function pickFolderMac(title: string) {
    const { stdout } = await execFileAsync("osascript", [
        "-e",
        `set chosenFolder to POSIX path of (choose folder with prompt "${title.replace(/"/g, '\\"')}")`,
        "-e",
        "return chosenFolder",
    ]);
    return normalizeSelectedPath(stdout);
}

async function pickFolderLinux(title: string) {
    const linuxPickers: Array<{ command: string; args: string[] }> = [
        {
            command: "zenity",
            args: ["--file-selection", "--directory", `--title=${title}`],
        },
        {
            command: "kdialog",
            args: ["--getexistingdirectory", "", "--title", title],
        },
    ];

    let lastError: unknown = null;
    for (const picker of linuxPickers) {
        try {
            const { stdout } = await execFileAsync(picker.command, picker.args);
            const selected = normalizeSelectedPath(stdout);
            if (selected) {
                return selected;
            }
        } catch (error) {
            lastError = error;
        }
    }
    throw lastError || new Error("No supported native folder picker is available.");
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const body = await req.json().catch(() => ({}));
    const title = String(body?.title || "Select workspace folder").trim();
    const initialPath = String(body?.initialPath || "").trim();

    try {
        let selected = "";
        if (process.platform === "win32") {
            selected = await pickFolderWindows(initialPath);
        } else if (process.platform === "darwin") {
            selected = await pickFolderMac(title);
        } else {
            selected = await pickFolderLinux(title);
        }
        if (!selected) {
            return NextResponse.json({ cancelled: true, supported: true });
        }
        return NextResponse.json({ cancelled: false, supported: true, path: selected });
    } catch (error) {
        const detail = error instanceof Error ? error.message : "Folder picker unavailable";
        return NextResponse.json({ cancelled: false, supported: false, error: detail }, { status: 501 });
    }
}
