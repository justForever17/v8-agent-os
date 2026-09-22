type JsonRecord = Record<string, unknown>;

export type SessionStateErrorPayload = {
    error: {
        code: string;
        message: string;
        retryable: true;
        upstreamStatus?: number;
    };
};

export async function readSessionStateError(
    response: Response,
    fallbackMessage = "本地运行状态数据库暂时不可用，已有状态未被覆盖。请稍后重试。",
): Promise<SessionStateErrorPayload> {
    const payload = await response.json().catch(() => ({}));
    const record = payload && typeof payload === "object" ? payload as JsonRecord : {};
    const detail = record.detail && typeof record.detail === "object"
        ? record.detail as JsonRecord
        : {};
    const nestedError = record.error && typeof record.error === "object"
        ? record.error as JsonRecord
        : {};
    const code = String(detail.code || nestedError.code || record.code || "state_database_unavailable").trim();
    const message = String(detail.message || nestedError.message || record.message || fallbackMessage).trim();
    return {
        error: {
            code: code || "state_database_unavailable",
            message: message || fallbackMessage,
            retryable: true,
            upstreamStatus: response.status,
        },
    };
}
