import { normalizeSessionToolResultStatus, type SessionToolResultStatus } from "./contract.js";

export type ClientToolSurfaceStatus = SessionToolResultStatus;

export interface ClientToolSurface {
    title: string;
    status: ClientToolSurfaceStatus;
    summary: string;
    progress?: string;
    actionable?: string;
    refIds: string[];
}

export interface BuildClientToolSurfaceInput {
    toolName: string;
    state?: "call" | "result" | string;
    result?: unknown;
    resultStatus?: SessionToolResultStatus | string;
    resultReasonCode?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as Record<string, unknown>;
    }
    if (typeof value !== "string") {
        return null;
    }
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

function toText(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value ?? "");
    } catch {
        return String(value ?? "");
    }
}

function compactText(value: unknown, limit = 180): string {
    const text = redactClientText(String(value ?? "")).replace(/\s+/g, " ").trim();
    if (!text) {
        return "";
    }
    return text.length > limit ? `${text.slice(0, Math.max(1, limit - 3)).trimEnd()}...` : text;
}

function redactClientText(value: string): string {
    return String(value ?? "")
        .replace(/\b[A-Za-z]:\\(?:Users|Projects|ProgramData|Windows|temp|Temp)[^\s,，;；)）\]}]*/gi, "[local path]")
        .replace(/\bactiveWorkspaceRoot=\[local path\]/gi, "activeWorkspaceRoot=[hidden]")
        .replace(/\bworkspacePath=\[local path\]/gi, "workspacePath=[hidden]");
}

function visibleLines(text: string): string[] {
    return text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .filter((line) => !/^(```|---)$/.test(line))
        .filter((line) => !/^\[scenario:[^\]]+\]$/i.test(line));
}

function pickLine(text: string, patterns: RegExp[]): string {
    const lines = visibleLines(text);
    for (const pattern of patterns) {
        const match = lines.find((line) => pattern.test(line));
        if (match) {
            return match;
        }
    }
    return lines[0] || "";
}

function pickMatchingLine(text: string, patterns: RegExp[]): string {
    const lines = visibleLines(text);
    for (const pattern of patterns) {
        const match = lines.find((line) => pattern.test(line));
        if (match) {
            return match;
        }
    }
    return "";
}

function pickRecordText(record: Record<string, unknown> | null, keys: string[]): string {
    if (!record) {
        return "";
    }
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            return value;
        }
    }
    return "";
}

function hasFailureLine(resultText: string): boolean {
    return visibleLines(resultText).some((line) => {
        if (/^[-*]\s+/.test(line)) {
            return false;
        }
        return (
            /^(status|result)[:：]\s*(failed|failure|error|exception)\b(?!\s*[=:])/i.test(line)
            || /^(failed|failure|error|exception)[:：\s]/i.test(line)
            || /^(失败|错误)[:：\s]/.test(line)
        );
    });
}

function extractSummary(result: unknown): string {
    if (typeof result === "string") {
        return compactText(
            pickLine(result, [
                /^(摘要|结果|答案|关键发现|正文内容|输出|状态|风险|限制|下一步|Summary|Result|Answer|Key findings|Content|Output|Status|Risk|Limitations|Next)[:：]/i,
            ]),
        );
    }
    const record = asRecord(result);
    const picked = pickRecordText(record, [
        "summary",
        "message",
        "statusMessage",
        "answer",
        "result",
        "error",
        "status",
    ]);
    if (picked) {
        return compactText(picked);
    }
    const fallbackText = toText(result);
    if (fallbackText.startsWith("{") || fallbackText.startsWith("[")) {
        return "";
    }
    return compactText(fallbackText);
}

function extractActionable(resultText: string, record: Record<string, unknown> | null): string | undefined {
    const direct = pickRecordText(record, ["recommendedNextAction", "nextAction", "actionable"]);
    if (direct) {
        return compactText(direct, 160);
    }
    const line = pickMatchingLine(resultText, [/^(下一步|Next|Action)[:：]/i]);
    return line ? compactText(line, 160) : undefined;
}

function extractProgress(record: Record<string, unknown> | null): string | undefined {
    if (!record) {
        return undefined;
    }
    const progress = record.progress;
    if (typeof progress === "string" && progress.trim()) {
        return compactText(progress, 80);
    }
    const completed = record.completed ?? record.done ?? record.completedCount;
    const total = record.total ?? record.totalCount ?? record.targetCount;
    if (completed !== undefined && total !== undefined) {
        return `${completed}/${total}`;
    }
    return undefined;
}

export function extractClientToolRefIds(resultText: string, maxRefs = 4): string[] {
    const refs = new Set<string>();
    const patterns = [
        /["']?\b(?:rawRef|detailRef|sectionRef|skillRef|relativeFileRef|memoryRef|answerPackRef|chunkRef|fileRef|episodeId|handoffId|jobId|artifactId)\b["']?\s*[:=]\s*["']?([^"'`,\s，)）\]}]+)/gi,
        /\btoolobs:\/\/[^"'`,\s，)）\]}]+/gi,
    ];
    for (const pattern of patterns) {
        for (const match of resultText.matchAll(pattern)) {
            refs.add(match[1] || match[0]);
            if (refs.size >= maxRefs) {
                return Array.from(refs);
            }
        }
    }
    return Array.from(refs);
}

function resolveStatus(
    state: string | undefined,
    resultText: string,
    record: Record<string, unknown> | null,
    resultStatus?: string,
): ClientToolSurfaceStatus {
    const authoritativeStatus = normalizeSessionToolResultStatus(resultStatus);
    if (authoritativeStatus && authoritativeStatus !== "unknown") {
        return authoritativeStatus;
    }
    const normalizedState = String(state || "").toLowerCase();
    const statusText = String(record?.status || record?.state || "").toLowerCase();
    const combined = `${statusText}\n${resultText.slice(0, 2000)}`.toLowerCase();
    if (/(timed_out|timeout|deadline_exceeded|超时)/.test(combined)) {
        return "timed_out";
    }
    if (/(terminated|cancelled|canceled|interrupted|stopped|已终止|已取消)/.test(combined)) {
        return "terminated";
    }
    if (/(command_session_required|command_session_redirect)/.test(combined)) {
        return "waiting";
    }
    if (/(unsafe_unobserved|blocked|safety_blocked|拒绝|阻断)/.test(combined)) {
        return "blocked";
    }
    if (/(stateful_unobserved|waiting|approval|ask_user|等待|审批)/.test(combined)) {
        return "waiting";
    }
    if (
        /^(failed|failure|error|exception)$/.test(statusText)
        || hasFailureLine(resultText)
    ) {
        return "failed";
    }
    if (normalizedState === "result" || normalizedState === "completed") {
        return "completed";
    }
    if (normalizedState === "call" || normalizedState === "running") {
        return "running";
    }
    return "unknown";
}

export function buildClientToolSurface(input: BuildClientToolSurfaceInput): ClientToolSurface {
    const toolName = String(input.toolName || "tool").trim() || "tool";
    const resultText = toText(input.result);
    const record = asRecord(input.result);
    return {
        title: toolName,
        status: resolveStatus(input.state, resultText, record, input.resultStatus),
        summary: extractSummary(input.result),
        progress: extractProgress(record),
        actionable: extractActionable(resultText, record),
        refIds: extractClientToolRefIds(resultText),
    };
}
