/** Transport failures are observations, never authoritative run failures. */
export function chatTransportError(code: string, message: string) {
    return Object.assign(new Error(message), {
        code, unknownOutcome: true, retryable: false, recovery: 'resync',
    });
}

export async function readChatStream(
    body: ReadableStream<Uint8Array>,
    onEvent: (event: unknown) => void,
    isCurrent: () => boolean,
) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let terminal = false;
    const accept = (line: string) => {
        if (!line.trim()) return;
        let raw: Record<string, unknown>;
        try {
            raw = JSON.parse(line);
        } catch {
            throw chatTransportError('engine_stream_invalid', '连接数据不完整，请同步当前任务状态。');
        }
        if (raw?.type === 'transport_error') {
            throw chatTransportError(String(raw.code || 'engine_stream_disconnected'), String(raw.error || '连接中断，请同步当前任务状态。'));
        }
        onEvent(raw);
        terminal ||= raw?.type === 'done' || raw?.type === 'error';
        if (raw?.type === 'error') {
            throw new Error(String(raw.error || '任务返回错误，请查看当前任务状态。'));
        }
    };
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (!isCurrent()) throw new DOMException('Detached conversation', 'AbortError');
            buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) accept(line);
            if (done) break;
        }
        accept(buffer);
        if (!terminal) {
            throw chatTransportError('engine_stream_disconnected', '连接中断，执行结果尚未确认。请同步当前任务状态。');
        }
    } finally {
        // Do not keep a failed reader (or its upstream connection) alive.
        void reader.cancel().catch(() => undefined);
        reader.releaseLock();
    }
}
