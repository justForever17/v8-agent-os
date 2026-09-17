export function rpaRunPresentation(status: unknown) {
    const value = String(status || "").trim().toLowerCase();
    if (value === "preparing") {
        return { key: "web.rpa.preparing", tone: "info", active: false } as const;
    }
    if (["completed", "completed_with_fallback", "completed_via_computer_use_primary"].includes(value)) {
        return { key: "web.runControl.status.completed", tone: "success", active: false } as const;
    }
    if (["running", "running_robot", "running_computer_use", "running_computer_use_primary", "resuming", "started", "queued", "pending"].includes(value)) {
        return { key: value === "queued" || value === "pending" ? "web.runControl.status.queued" : "web.runControl.status.running", tone: "info", active: true } as const;
    }
    if (["review_required", "waiting_approval", "awaiting_approval"].includes(value)) {
        return { key: "web.runControl.status.waitingApproval", tone: "warning", active: false } as const;
    }
    if (["cancelled", "canceled", "interrupted"].includes(value)) {
        return { key: "web.runControl.status.cancelled", tone: "warning", active: false } as const;
    }
    if (["failed", "blocked", "compile_blocked", "rejected"].includes(value)) {
        return { key: "web.runControl.status.failed", tone: "error", active: false } as const;
    }
    return { key: "web.toolCard.unknown", tone: "warning", active: false } as const;
}
