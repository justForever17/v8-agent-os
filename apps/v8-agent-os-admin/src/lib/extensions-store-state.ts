export type StoreProvider = "international" | "modelscope";
export type StoreKind = "skills" | "mcp";
export function storeTarget(provider: StoreProvider, kind: StoreKind, item: { id: string; skillId?: string; source?: string }): string {
    const source = kind === "mcp" ? "" : provider === "modelscope" ? item.skillId || item.id : item.source || "";
    const id = kind === "mcp" ? item.id : provider === "modelscope" ? item.skillId || item.id : `${source}@${item.skillId || ""}`;
    return `${provider}:${kind}:${source}:${id}`;
}
/** Shared cache flights survive view changes; only the current view accepts results. */
export class StoreRequestOwner {
    private generation = 0;
    private target = "";
    begin(target: string) { this.target = target; return ++this.generation; }
    invalidate() { this.target = ""; ++this.generation; }
    accepts(generation: number) { return this.generation === generation; }
    isCurrent(target: string) { return this.target === target; }
    capture() { return this.generation; }
}
