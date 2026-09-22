export type PluginDetailRequestToken = Readonly<{
    pluginId: string;
    revision: number;
    signal: AbortSignal;
}>;

export function createPluginDetailRequestCoordinator() {
    let revision = 0;
    let current: { pluginId: string; revision: number; controller: AbortController } | null = null;

    const isCurrent = (request: PluginDetailRequestToken) => Boolean(
        current
        && !request.signal.aborted
        && current.pluginId === request.pluginId
        && current.revision === request.revision,
    );

    return {
        begin(pluginId: string): PluginDetailRequestToken {
            current?.controller.abort();
            const controller = new AbortController();
            current = { pluginId, revision: ++revision, controller };
            return { pluginId, revision, signal: controller.signal };
        },
        isCurrent,
        commit(request: PluginDetailRequestToken, apply: () => void) {
            if (!isCurrent(request)) return false;
            apply();
            return true;
        },
        cancel(request?: PluginDetailRequestToken) {
            if (!current || (request && !isCurrent(request))) return false;
            current.controller.abort();
            current = null;
            return true;
        },
    };
}

export type PluginDetailRequestCoordinator = ReturnType<typeof createPluginDetailRequestCoordinator>;
