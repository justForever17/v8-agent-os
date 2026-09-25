import type { Action, Field, Surface } from './surface.js';

export const MODEL_TABS = [
  '全部 (All)',
  '对话文本 (Chat)',
  '多模态视觉 (Vision)',
  '深度推理 (Reasoning)',
  '媒体生成 (Media)',
  '向量与重排 (Embedding)',
];

export const ROLE_TABS = [
  'Supervisor (主智能体)',
  'Subagent (子智能体)',
  'Runtime (轻量执行)',
  'Vision (多模态视觉)',
];

export const ROLE_KEYS = ['supervisor', 'subagent', 'runtime', 'vision'] as const;

export const ROLE_DESCRIPTIONS = [
  '主编排智能体：负责多轮对话、用户意图理解、复杂任务规划与子智能体调度。',
  '子智能体 (Subagent)：负责执行具体代码编写、测试运行、分支探索等独立子任务。',
  '轻量执行运行时 (Runtime/Fast)：负责快速工具调用、状态检查、规划与轻量执行。',
  '视觉多模态 (Vision)：负责图片解析、UI 界面识别、设计图比对等多模态感知任务。',
];

export const PRESET_PROVIDERS = [
  {
    id: 'openai',
    name: 'OpenAI (官方)',
    baseUrl: 'https://api.openai.com/v1',
    presetModels: ['gpt-4o', 'gpt-4o-mini', 'o3-mini', 'o1', 'gpt-4.5-preview'],
  },
  {
    id: 'deepseek',
    name: 'DeepSeek (深度求索)',
    baseUrl: 'https://api.deepseek.com/v1',
    presetModels: ['deepseek-chat', 'deepseek-reasoner'],
  },
  {
    id: 'dashscope',
    name: 'Alibaba DashScope (通义千问 / Qwen)',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    presetModels: ['qwen-max', 'qwen-plus', 'qwen-turbo', 'qwen2.5-72b-instruct'],
  },
  {
    id: 'anthropic',
    name: 'Anthropic (Claude)',
    baseUrl: 'https://api.anthropic.com',
    presetModels: ['claude-3-7-sonnet-latest', 'claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest'],
  },
  {
    id: 'ollama',
    name: 'Ollama (本地私有大模型)',
    baseUrl: 'http://127.0.0.1:11434/v1',
    presetModels: ['qwen2.5:7b', 'deepseek-r1:7b', 'llama3.3:70b', 'qwen2.5-coder:7b'],
  },
  {
    id: 'gemini-api',
    name: 'Google Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    presetModels: ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash'],
  },
  {
    id: 'siliconflow',
    name: 'SiliconFlow (硅基流动)',
    baseUrl: 'https://api.siliconflow.cn/v1',
    presetModels: ['deepseek-ai/DeepSeek-V3', 'deepseek-ai/DeepSeek-R1', 'Qwen/Qwen2.5-72B-Instruct'],
  },
  {
    id: 'openrouter',
    name: 'OpenRouter (聚合网关)',
    baseUrl: 'https://openrouter.ai/api/v1',
    presetModels: ['anthropic/claude-3.7-sonnet', 'openai/gpt-4o', 'deepseek/deepseek-chat'],
  },
  {
    id: 'custom',
    name: '自定义 OpenAI 兼容接口',
    baseUrl: '',
    presetModels: [],
  },
];

export function filterModelsByTab(models: any[], tabIndex: number): any[] {
  if (tabIndex === 0) return models;
  if (tabIndex === 1) {
    return models.filter(m => {
      const type = String(m.type || '').toUpperCase();
      const caps = m.capabilities || [];
      return ['TEXT', 'CHAT'].includes(type) || caps.includes('chat');
    });
  }
  if (tabIndex === 2) {
    return models.filter(m => {
      const type = String(m.type || '').toUpperCase();
      const caps = m.capabilities || [];
      return ['MULTIMODAL', 'VISION'].includes(type) || caps.includes('vision');
    });
  }
  if (tabIndex === 3) {
    return models.filter(m => {
      const type = String(m.type || '').toUpperCase();
      const caps = m.capabilities || [];
      return type === 'REASONING' || caps.includes('reasoning') || Boolean(m.reasoningEffortControl) || Boolean(m.thinkingControl);
    });
  }
  if (tabIndex === 4) {
    return models.filter(m => {
      const type = String(m.type || '').toUpperCase();
      return ['IMAGE', 'VIDEO', 'VOICE', 'AUDIO', 'MUSIC', 'WORKFLOW', 'MODEL3D'].includes(type);
    });
  }
  if (tabIndex === 5) {
    return models.filter(m => {
      const type = String(m.type || '').toUpperCase();
      return ['EMBEDDING', 'RERANK'].includes(type);
    });
  }
  return models;
}

export async function assignRoleModel(surface: Surface, role: string, modelRef: string) {
  const plan = await surface.client.api('/v1/config-broker/roles/prepare', {
    method: 'POST',
    body: { role, modelRef },
  });
  if (plan?.transactionId && plan?.planDigest) {
    await surface.client.api(`/v1/config-broker/transactions/${encodeURIComponent(plan.transactionId)}/commit`, {
      method: 'POST',
      body: { planDigest: plan.planDigest },
    });
  }
}

export async function setDefaultModel(surface: Surface, modelRef: string, category = '') {
  await surface.client.api('/v1/models/default-model', {
    method: 'POST',
    body: { modelRef, category },
  });
}

export async function modelsHub(surface: Surface, activeTab = 0) {
  let rolesData: any = { roles: [] };
  let controlPlane: any = { models: [] };
  try {
    const [rolesRes, cpRes] = await Promise.all([
      surface.client.api('/v1/config-broker/roles').catch(() => ({ roles: [] })),
      surface.client.api('/v1/models/control-plane').catch(() => ({ models: [] })),
    ]);
    rolesData = rolesRes || { roles: [] };
    controlPlane = cpRes || { models: [] };
  } catch {
    // fallback gracefully
  }

  const allModels: any[] = Array.isArray(controlPlane?.models) ? controlPlane.models : [];
  const assignedRoles = Array.isArray(rolesData?.roles) ? rolesData.roles : [];

  const filtered = filterModelsByTab(allModels, activeTab);

  const modelActions: Action[] = filtered.map(model => {
    const modelRef = model.modelRef || `${model.providerId}/${model.modelId}`;
    const boundRoles = assignedRoles.filter((r: any) => r.modelRef === modelRef).map((r: any) => r.label || r.role);
    const roleBadge = boundRoles.length ? ` [★ ${boundRoles.join(' · ')}]` : '';
    const ctx = model.contextWindow ? `${Math.round(model.contextWindow / 1000)}K` : 'auto';
    const status = model.runtimeReady !== false && model.eligibility?.eligible !== false ? '●' : '○';
    const label = `${status} [${model.providerName || model.providerId}] ${model.modelId} · ${ctx}${roleBadge}`;

    return {
      label,
      run: () => modelActionMenu(surface, model, assignedRoles, activeTab),
    };
  });

  const lines = [
    `当前分类：${MODEL_TABS[activeTab]} · 已注册模型 ${filtered.length} / ${allModels.length} 个`,
    '←→ 切换模型类型 · ↑↓ 选择模型 · Enter 快速配置/设为默认',
  ];
  if (!allModels.length) {
    lines.push('系统暂未绑定模型，建议通过下方“＋ 快速注册/连接新模型”向导添加。');
  }

  const actions: Action[] = [
    ...modelActions,
    { label: '＋ 快速注册/连接新模型 (向导式选择 Provider)', run: () => connectModelWizard(surface) },
    { label: '📑 角色选项卡模型分配 (Supervisor / Subagent / Runtime)', run: () => roleModelAssignmentTabs(surface, 0) },
    { label: '💰 累计 Token 与预算设置', run: () => surface.budgets() },
    { label: '📊 近 24 小时用量监控', run: () => surface.modelUsage() },
    { label: '返回对话', run: () => surface.close(true) },
  ];

  surface.open('模型管理中心 / Model Hub', lines, actions, {
    tabs: MODEL_TABS,
    activeTab,
    onTabChange: async (nextTab: number) => {
      await modelsHub(surface, nextTab);
    },
  });
}

export function modelActionMenu(surface: Surface, model: any, assignedRoles: any[], returnTab = 0) {
  const modelRef = model.modelRef || `${model.providerId}/${model.modelId}`;
  const provider = model.providerName || model.providerId;
  const boundRoles = assignedRoles.filter((r: any) => r.modelRef === modelRef).map((r: any) => r.label || r.role);

  const lines = [
    `模型名称：[${provider}] ${model.modelId}`,
    `模型引用：${modelRef}`,
    `上下文窗口：${model.contextWindow || '未知'} · 单次输出：${model.outputTokenMode || (model.maxTokens ? `fixed ${model.maxTokens}` : 'auto')}`,
    `当前绑定角色：${boundRoles.length ? boundRoles.join('、') : '暂未绑定特定角色'}`,
    '',
    '选择操作后按 Enter 即可快速生效：',
  ];

  const actions: Action[] = [
    {
      label: '★ 设为全局默认模型 (Supervisor + Subagent)',
      run: async () => {
        await setDefaultModel(surface, modelRef);
        surface.client.notice = `已将 [${provider}] ${model.modelId} 设为全局默认模型！`;
        await modelsHub(surface, returnTab);
      },
    },
    {
      label: '👉 分配给 Supervisor (主智能体编排)',
      run: async () => {
        await assignRoleModel(surface, 'supervisor', modelRef);
        surface.client.notice = `已将 [${provider}] ${model.modelId} 分配给 Supervisor！`;
        await modelsHub(surface, returnTab);
      },
    },
    {
      label: '👉 分配给 Subagent (子智能体执行)',
      run: async () => {
        await assignRoleModel(surface, 'subagent', modelRef);
        surface.client.notice = `已将 [${provider}] ${model.modelId} 分配给 Subagent！`;
        await modelsHub(surface, returnTab);
      },
    },
    {
      label: '👉 分配给 Runtime (轻量执行运行时)',
      run: async () => {
        await assignRoleModel(surface, 'runtime', modelRef);
        surface.client.notice = `已将 [${provider}] ${model.modelId} 分配给 Runtime！`;
        await modelsHub(surface, returnTab);
      },
    },
    {
      label: '👉 分配给 Vision (多模态视觉感知)',
      run: async () => {
        await assignRoleModel(surface, 'vision', modelRef);
        surface.client.notice = `已将 [${provider}] ${model.modelId} 分配给 Vision！`;
        await modelsHub(surface, returnTab);
      },
    },
    {
      label: '⚙️ 配置单次输出 Token 上限 (auto / fixed)',
      run: () => {
        surface.form('输出长度配置', [
          { key: 'mode', label: '输出模式（auto / fixed）', value: model.outputTokenMode || (model.maxTokens ? 'fixed' : 'auto') },
          { key: 'maxTokens', label: 'fixed 模式输出 Token 上限', value: String(model.maxTokens || '') },
        ], async values => {
          const mode = values.mode.trim();
          if (!['auto', 'fixed'].includes(mode)) throw new Error('请选择 auto 或 fixed');
          const maxTokens = mode === 'fixed' ? Number(values.maxTokens) : 0;
          if (mode === 'fixed' && (!Number.isInteger(maxTokens) || maxTokens <= 0)) throw new Error('fixed 模式需要正整数 Token 上限');
          const patch = { outputTokenMode: mode, ...(mode === 'fixed' ? { maxTokens } : {}) };
          await surface.client.api('/v1/models/bindings', { method: 'PUT', body: { providerId: model.providerId, modelId: model.modelId, model: patch } });
          surface.client.notice = `已更新 [${provider}] ${model.modelId} 的输出参数！`;
          await modelsHub(surface, returnTab);
        });
      },
    },
    {
      label: '返回模型列表',
      run: () => modelsHub(surface, returnTab),
    },
  ];

  surface.open('模型操作 · 快速配置', lines, actions);
}

export async function roleModelAssignmentTabs(surface: Surface, activeRoleTab = 0) {
  let rolesData: any = { roles: [] };
  let controlPlane: any = { models: [] };
  try {
    const [rolesRes, cpRes] = await Promise.all([
      surface.client.api('/v1/config-broker/roles').catch(() => ({ roles: [] })),
      surface.client.api('/v1/models/control-plane').catch(() => ({ models: [] })),
    ]);
    rolesData = rolesRes || { roles: [] };
    controlPlane = cpRes || { models: [] };
  } catch {
    // fallback gracefully
  }

  const allModels: any[] = Array.isArray(controlPlane?.models) ? controlPlane.models : [];
  const assignedRoles: any[] = Array.isArray(rolesData?.roles) ? rolesData.roles : [];

  const targetRoleKey = ROLE_KEYS[activeRoleTab];
  const targetRoleDef = assignedRoles.find((r: any) => r.role === targetRoleKey);
  const currentModelRef = targetRoleDef?.modelRef || '';
  const currentStatus = targetRoleDef?.status || 'unbound';

  const lines = [
    `角色名称：${ROLE_TABS[activeRoleTab]}`,
    `当前绑定：${currentModelRef || '尚未绑定模型'} · ${currentStatus === 'ready' ? '● 已就绪' : '○ 待就绪'}`,
    `角色职能：${ROLE_DESCRIPTIONS[activeRoleTab]}`,
    '',
    '按 ←→ 切换配置角色 · 按 ↑↓ 选择可用模型 · 按 Enter 一键绑定',
  ];

  const modelActions: Action[] = allModels.map(model => {
    const modelRef = model.modelRef || `${model.providerId}/${model.modelId}`;
    const isCurrent = modelRef === currentModelRef;
    const badge = isCurrent ? '● [当前绑定] ' : '○ [设为该角色] ';
    const ctx = model.contextWindow ? `${Math.round(model.contextWindow / 1000)}K` : 'auto';
    const label = `${badge}[${model.providerName || model.providerId}] ${model.modelId} · ${ctx}`;

    return {
      label,
      run: async () => {
        if (!isCurrent) {
          await assignRoleModel(surface, targetRoleKey, modelRef);
          surface.client.notice = `已成功将 [${model.providerName || model.providerId}] ${model.modelId} 绑定为 ${ROLE_TABS[activeRoleTab]} 模型！`;
        }
        await roleModelAssignmentTabs(surface, activeRoleTab);
      },
    };
  });

  const actions: Action[] = [
    ...modelActions,
    { label: '＋ 快速注册新模型...', run: () => connectModelWizard(surface) },
    { label: '📋 返回模型管理列表', run: () => modelsHub(surface, 0) },
    { label: '返回对话', run: () => surface.close(true) },
  ];

  surface.open('角色模型分配 / Role Assignment', lines, actions, {
    tabs: ROLE_TABS,
    activeTab: activeRoleTab,
    onTabChange: async (nextTab: number) => {
      await roleModelAssignmentTabs(surface, nextTab);
    },
  });
}

export function connectModelWizard(surface: Surface) {
  const lines = [
    '请选择你要连接的 Provider 服务商：',
    '已预置官方接口地址与推荐模型；也支持自定义任何 OpenAI 兼容 API。',
  ];

  const actions: Action[] = PRESET_PROVIDERS.map(provider => ({
    label: provider.name,
    run: () => selectModelWizard(surface, provider),
  }));

  actions.push({
    label: '返回模型中心',
    run: () => modelsHub(surface, 0),
  });

  surface.open('连接与注册模型 · 第 1/2 步：选择 Provider', lines, actions);
}

export function selectModelWizard(surface: Surface, provider: typeof PRESET_PROVIDERS[number]) {
  const lines = [
    `已选 Provider：${provider.name}`,
    '请选择预置推荐模型，或输入自定义模型名称：',
  ];

  const actions: Action[] = provider.presetModels.map(modelId => ({
    label: `${modelId}`,
    run: () => enterCredentialsWizard(surface, provider, modelId),
  }));

  actions.push(
    {
      label: '✎ 自定义输入模型 ID...',
      run: () => enterCredentialsWizard(surface, provider, ''),
    },
    {
      label: '← 返回选择 Provider',
      run: () => connectModelWizard(surface),
    },
  );

  surface.open(`连接模型 · 第 2/2 步：选择模型 (${provider.name})`, lines, actions);
}

export function enterCredentialsWizard(surface: Surface, provider: typeof PRESET_PROVIDERS[number], defaultModelId = '') {
  const fields: Field[] = [
    { key: 'providerId', label: 'Provider 标识', value: provider.id },
    { key: 'modelId', label: '模型 ID', value: defaultModelId },
    { key: 'baseUrl', label: 'API 接口地址（留空使用默认）', value: provider.baseUrl },
    {
      key: 'apiKey',
      label: provider.id === 'ollama' ? 'API 密钥（本地 Ollama 可留空）' : 'API 密钥（隐藏输入，留空复用已有凭据）',
      value: '',
      secret: true,
    },
  ];

  surface.form('连接并注册模型', fields, async values => {
    const providerId = values.providerId.trim();
    const modelId = values.modelId.trim();
    if (!providerId || !modelId) throw new Error('Provider 标识与模型 ID 不能为空');

    try {
      await surface.client.api('/v1/models/connect', {
        method: 'POST',
        body: values,
        timeoutMs: 60000,
      });
    } finally {
      values.apiKey = '';
    }

    const modelRef = `${providerId}/${modelId}`;

    surface.open('模型注册成功', [
      `已成功注册模型：[${providerId}] ${modelId}`,
      `接口地址：${values.baseUrl || '默认地址'}`,
      '',
      '是否立即将该模型设为全局默认模型 (Supervisor + Subagent)？',
    ], [
      {
        label: '★ 立即设为全局默认模型',
        run: async () => {
          await setDefaultModel(surface, modelRef);
          surface.client.notice = `已将 [${providerId}] ${modelId} 设为全局默认模型！`;
          await modelsHub(surface, 0);
        },
      },
      {
        label: '📑 为指定角色单独配置模型 (Supervisor / Subagent / Runtime)',
        run: () => roleModelAssignmentTabs(surface, 0),
      },
      {
        label: '📋 返回模型管理中心',
        run: () => modelsHub(surface, 0),
      },
    ]);
  }, [
    `Provider: ${provider.name}`,
    provider.id === 'ollama' ? '提示：本地 Ollama 需确保服务已运行 (默认 http://127.0.0.1:11434)' : '提示：密钥由 Engine 安全加密存储，终端不会明文回显。',
    'Esc 取消返回 · F9 确认连接',
  ]);
}
