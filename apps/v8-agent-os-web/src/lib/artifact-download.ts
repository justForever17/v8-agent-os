const DOWNLOAD_FLAG = "download";

export function artifactDownloadUrl(value: string) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
        const base = typeof window !== "undefined" ? window.location.origin : "http://v8os.local";
        const parsed = new URL(raw, base);
        parsed.searchParams.set(DOWNLOAD_FLAG, "1");
        return raw.startsWith("/") ? `${parsed.pathname}${parsed.search}${parsed.hash}` : parsed.toString();
    } catch {
        return raw;
    }
}

export function downloadArtifact(value: string, filename = "") {
    if (typeof document === "undefined") return;
    const url = artifactDownloadUrl(value);
    if (!url) return;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = String(filename || "").trim();
    anchor.rel = "noopener noreferrer";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
}
