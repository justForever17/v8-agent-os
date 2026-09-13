export async function readLocalAdminBaseUrl(signal?: AbortSignal) {
    const response = await fetch("/api/connection?local=1", { cache: "no-store", signal });
    if (!response.ok) throw new Error("Local Admin connection unavailable");
    const payload = await response.json();
    const value = String(payload?.connection?.adminBaseUrl || "");
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid Admin connection");
    return value.replace(/\/+$/, "");
}
