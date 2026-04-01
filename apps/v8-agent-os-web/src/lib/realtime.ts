type JsonRecord = Record<string, unknown>;

export interface LegacyChatEvent extends JsonRecord {
    type: string;
}

export interface RuntimeEnvelope extends JsonRecord {
    kind?: string;
    topic?: string;
    payload?: LegacyChatEvent | JsonRecord;
}

function buildComputerUseProgressEvent(envelope: RuntimeEnvelope, payloadRecord: JsonRecord) {
    const topic = String(envelope.topic || "");
    const shortReason = (() => {
        const error = typeof payloadRecord.error === "string" ? payloadRecord.error : "";
        const verificationReason = typeof (payloadRecord.verification as JsonRecord | undefined)?.reason === "string"
            ? String((payloadRecord.verification as JsonRecord).reason || "")
            : "";
        const reason = typeof payloadRecord.reason === "string" ? payloadRecord.reason : "";
        return error || verificationReason || reason;
    })();
    let label = "";
    if (topic === "computer_use.observation.captured") {
        label = "Computer Use 正在观察当前桌面";
    } else if (topic === "computer_use.plan.started") {
        label = `Computer Use 开始执行 ${(payloadRecord.stepCount as number | undefined) || 0} 步计划`;
    } else if (topic === "computer_use.step.started") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步开始：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.heartbeat") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步仍在执行：${String(payloadRecord.action || "")}（已持续 ${(payloadRecord.elapsedSeconds as number | undefined) || 0}s）`;
    } else if (topic === "computer_use.step.waiting_for_window") {
        label = `Computer Use 正在等待 ${String(payloadRecord.appName || payloadRecord.appId || payloadRecord.expectedTitle || "目标窗口")} 出现`;
    } else if (topic === "computer_use.step.recovery_started") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步开始结构化恢复：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.recovery_completed") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步完成结构化恢复：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.retrying") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步重试中：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.review_required") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步需要人工复核：${String(payloadRecord.action || "")}${shortReason ? `，原因：${shortReason}` : ""}`;
    } else if (topic === "computer_use.step.coordinate_fallback_started") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步启动坐标回退：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.visual_fallback_started") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步启动视觉兜底：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.visual_recovery_started") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步开始视觉恢复：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.step.visual_recovery_failed") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步视觉恢复失败：${String(payloadRecord.action || "")}`;
    } else if (topic === "computer_use.action.settle_wait_started") {
        label = `Computer Use 正在等待界面稳定：${String(payloadRecord.actionType || "")}（超时 ${(payloadRecord.timeoutMs as number | undefined) || 0}ms）`;
    } else if (topic === "computer_use.action.settle_wait_completed") {
        label = `Computer Use 已确认界面稳定：${String(payloadRecord.actionType || "")}（耗时 ${(payloadRecord.elapsedMs as number | undefined) || 0}ms）`;
    } else if (topic === "computer_use.action.settle_wait_timeout") {
        label = `Computer Use 等待界面稳定超时：${String(payloadRecord.actionType || "")}${shortReason ? `，原因：${shortReason}` : ""}`;
    } else if (topic === "computer_use.step.update_requested") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步检测到重大偏差，已请求更新：${String(payloadRecord.action || "")}${shortReason ? `，原因：${shortReason}` : ""}`;
    } else if (topic === "computer_use.action.visual_guard_softened") {
        label = `Computer Use 发现视觉截图与结构化状态冲突，已改以结构化结果为准${shortReason ? `：${shortReason}` : ""}`;
    } else if (topic === "computer_use.step.completed") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步完成：${String(payloadRecord.action || "")}${typeof payloadRecord.elapsedSeconds === "number" ? `（${payloadRecord.elapsedSeconds}s）` : ""}`;
    } else if (topic === "computer_use.step.failed") {
        label = `Computer Use 第 ${(payloadRecord.index as number | undefined) || 0} 步失败：${String(payloadRecord.action || "")}${shortReason ? `，原因：${shortReason}` : ""}`;
    }
    if (!label) {
        return null;
    }
    return {
        type: "custom_event",
        name: "runtime_progress",
        data: {
            label,
            topic,
            ...payloadRecord,
        },
        kind: envelope.kind,
        topic,
        seq: (envelope as JsonRecord).seq,
        session_id: (envelope as JsonRecord).session_id,
        conversation_id: (envelope as JsonRecord).conversation_id,
        run_id: (envelope as JsonRecord).run_id,
        event_id: (envelope as JsonRecord).event_id,
        ts: (envelope as JsonRecord).ts,
    } satisfies LegacyChatEvent;
}

export function normalizeRealtimeEvent(raw: unknown): LegacyChatEvent | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }

    const direct = raw as LegacyChatEvent;
    if (typeof direct.type === "string") {
        return direct;
    }

    const envelope = raw as RuntimeEnvelope;
    if (typeof envelope.topic === "string" && envelope.payload && typeof envelope.payload === "object") {
        const payloadRecord = envelope.payload as JsonRecord;
        const computerUseProgress = buildComputerUseProgressEvent(envelope, payloadRecord);
        if (computerUseProgress) {
            return computerUseProgress;
        }

        if (envelope.topic === "approval.requested") {
            const request = (payloadRecord.request as JsonRecord | undefined) || {};
            const question =
                (typeof request.question === "string" && request.question) ||
                (typeof request.prompt === "string" && request.prompt) ||
                "我需要您的输入以继续执行任务。";
            const toolCallId =
                (typeof request.toolCallId === "string" && request.toolCallId) ||
                (typeof payloadRecord.approval_id === "string" && payloadRecord.approval_id) ||
                "";

            return {
                type: "custom_event",
                name: "ask_user",
                data: {
                    question,
                    toolCallId,
                    approvalId: typeof payloadRecord.approval_id === "string" ? payloadRecord.approval_id : undefined,
                    approvalKind: typeof payloadRecord.approval_kind === "string" ? payloadRecord.approval_kind : undefined,
                    interactionKind: typeof request.interactionKind === "string" ? request.interactionKind : undefined,
                    request,
                },
                kind: envelope.kind,
                topic: envelope.topic,
                seq: (envelope as JsonRecord).seq,
                session_id: (envelope as JsonRecord).session_id,
                conversation_id: (envelope as JsonRecord).conversation_id,
                run_id: (envelope as JsonRecord).run_id,
                event_id: (envelope as JsonRecord).event_id,
                ts: (envelope as JsonRecord).ts,
            };
        }

        if (["run.paused", "run.cancelled", "run.interrupted"].includes(envelope.topic)) {
            return {
                type: "custom_event",
                name: "run_controlled",
                data: {
                    topic: envelope.topic,
                    ...payloadRecord,
                },
                kind: envelope.kind,
                topic: envelope.topic,
                seq: (envelope as JsonRecord).seq,
                session_id: (envelope as JsonRecord).session_id,
                conversation_id: (envelope as JsonRecord).conversation_id,
                run_id: (envelope as JsonRecord).run_id,
                event_id: (envelope as JsonRecord).event_id,
                ts: (envelope as JsonRecord).ts,
            };
        }

        if (envelope.topic === "artifact.recorded") {
            return {
                type: "custom_event",
                name: "artifact_recorded",
                data: {
                    artifact: payloadRecord,
                },
                kind: envelope.kind,
                topic: envelope.topic,
                seq: (envelope as JsonRecord).seq,
                session_id: (envelope as JsonRecord).session_id,
                conversation_id: (envelope as JsonRecord).conversation_id,
                run_id: (envelope as JsonRecord).run_id,
                event_id: (envelope as JsonRecord).event_id,
                ts: (envelope as JsonRecord).ts,
            };
        }
    }

    if (envelope.payload && typeof envelope.payload === "object") {
        const payload = envelope.payload as LegacyChatEvent;
        if (typeof payload.type === "string") {
            return {
                ...payload,
                kind: envelope.kind,
                topic: envelope.topic,
                seq: (envelope as JsonRecord).seq,
                session_id: (envelope as JsonRecord).session_id,
                conversation_id: (envelope as JsonRecord).conversation_id,
                run_id: (envelope as JsonRecord).run_id,
                event_id: (envelope as JsonRecord).event_id,
                ts: (envelope as JsonRecord).ts,
            };
        }
    }

    return null;
}
