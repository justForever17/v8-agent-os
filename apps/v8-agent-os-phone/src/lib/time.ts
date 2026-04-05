export function formatRelativeTime(value?: string | number | null) {
    if (!value) return "刚刚";
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return "刚刚";
    const delta = Math.max(0, Date.now() - timestamp);
    const minutes = Math.floor(delta / 60000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days} 天前`;
    return new Date(timestamp).toLocaleDateString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export function formatClock(value?: string | number | null) {
    if (!value) return "";
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return "";
    return new Date(timestamp).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}
