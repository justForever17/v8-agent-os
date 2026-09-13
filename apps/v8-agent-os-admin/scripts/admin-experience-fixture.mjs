// Synthetic browser boundary; never reads a user's config or state.
export const sampleModel = { id: 'fixture-model', modelId: 'sample-chat', modelRef: 'fixture-provider::sample-chat', providerId: 'fixture-provider', type: 'TEXT', isEnabled: true, contextWindow: 32768, outputTokenMode: 'auto', provider: { name: 'Fixture provider', id: 'fixture-provider' } };
export const sampleProvider = { id: 'fixture-provider', name: 'Fixture provider', code: 'fixture-provider', type: 'API', apiStandard: 'openai', baseUrl: 'https://example.invalid/v1', isEnabled: true, hasCredential: true, channels: [{ id: 'default', apiStandard: 'openai', baseUrl: 'https://example.invalid/v1' }], models: [sampleModel] };
export const sampleClusters = ['global', 'a', 'b'].map((name, index) => ({
    clusterId: name === 'global' ? name : JSON.stringify(['workspace', name]), scopeKind: name === 'global' ? 'global' : 'workspace', workspaceKey: name === 'global' ? null : name,
    label: name === 'global' ? 'Global' : `Workspace ${name.toUpperCase()}`, writeScope: `workspace:${name}`,
    nodes: Array.from({ length: 12 }, (_, node) => ({ id: node ? `${name}-node-${node}` : 'shared', label: node ? `${name} node ${node}` : 'shared', type: 'concept' })),
    links: Array.from({ length: 11 }, (_, edge) => ({ relationId: `${name}-${edge}`, source: 'shared', target: `${name}-node-${edge + 1}`, label: 'USES', scope: index ? `workspace:${name}` : 'global', confidence: 1, version: 'fixture-1' })),
    meta: { totalEntities: 12, totalRelations: 11, renderedEntities: 12, renderedRelations: 11, truncated: false, version: 'fixture-1' },
}));
export function adminExperienceFixture(url) {
    const parsed = new URL(url), p = parsed.pathname;
    if (p === '/api/ui-preferences/theme') return { theme: 'light' };
    if (p === '/api/model-hub/bootstrap') return { providers: [sampleProvider], models: [sampleModel], hubEnvelope: { domain: 'model-hub', data: { models: [], providersOverview: [], summary: { providers: 1, models: 1 }, config: { governance: { enabled: true } } } }, defaultModel: { modelRef: sampleModel.modelRef }, catalog: { providers: [] }, audioConfig: {} };
    if (p === '/api/providers/fixture-provider') return sampleProvider;
    if (p === '/api/models') return [sampleModel];
    if (p === '/api/agents') return [{ id: 'fixture-agent', name: 'Fixture specialist', description: 'Research and summarize', model_id: sampleModel.modelRef, tools: [], tool_mode: 'explicit', enabled: true, system_prompt: 'Fixture prompt', capabilitySnapshot: {}, createdBy: 'human' }];
    if (p === '/api/agents/tool-surface') return { baselineSystemTools: [], runtimeManagedTools: [] };
    if (p === '/api/extensions/catalog') return { skills: { items: [] }, mcp: { servers: [] }, summary: {} };
    if (p === '/api/supervisor') return { name: 'Fixture supervisor', systemPrompt: 'Fixture prompt', model_id: sampleModel.modelRef, allowed_tools: [], locked_native_tools: [], runtime_managed_tools: [] };
    if (p === '/api/mcp/tools') return { mcpTools: [] };
    if (p === '/api/settings/vision-model') return { value: null, source: 'fixture' };
    if (p === '/api/memory/dashboard') return { graph: { top_entities: [], entities: 36, relations: 33 }, preferences: [], statistics: {}, config: {} };
    if (p === '/api/memory/knowledge' || p.includes('knowledge-resolution') || p.includes('/memory/documents')) return { items: [] };
    if (p === '/api/memory/knowledge-health') return { graph: { relations: 33 }, projection: {} };
    if (p === '/api/memory/graph') {
        const cluster = sampleClusters.find(item => item.clusterId === parsed.searchParams.get('clusterId'));
        if (parsed.searchParams.has('entity')) return { relations: (cluster?.links || []).map(edge => ({ ...edge, subject: edge.source, object: edge.target, predicate: edge.label })), total: 11, nextOffset: null };
        return { items: cluster ? [cluster] : sampleClusters, totalWorkspaces: 2, nextOffset: null, partial: false };
    }
    if (p.startsWith('/api/config-registry/')) return { domain: p.split('/').at(-1), source: 'synthetic-ui-fixture', savePath: 'fixture', warnings: [], advancedFields: [], reloadRequired: false, data: {
        jobs: [], hooks: [], projects: [], workspaces: [], defaults: {}, bindings: {}, modelParameters: {}, specialistRegistry: {}, delegation: {}, research: {},
        modelBindings: { discoveryModel: '', planningModel: '' }, executionPolicy: {}, bridge: { engineBaseUrl: 'http://127.0.0.1:1', internalSecret: '***', fixtureUnknown: { enabled: false, count: 0 } }, environmentProbe: { status: 'ready' }, desktopReadiness: { status: 'partial' }, dependencyStatus: [], runtimeInfo: {},
        customOpaqueFixture: { empty: [], zero: 0, flag: false, nested: { future: true } },
    } };
    if (p === '/api/admin-inbox') return { items: [], unreadCount: 0 };
    return undefined;
}
