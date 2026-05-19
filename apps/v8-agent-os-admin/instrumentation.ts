export async function register() {
    if (process.env.NEXT_RUNTIME && process.env.NEXT_RUNTIME !== "nodejs") {
        return;
    }

    const { warmDesktopLiveBridge } = await import("./src/lib/server/desktop-live-bridge");
    void warmDesktopLiveBridge().catch(() => undefined);
}
