import { randomUUID } from 'node:crypto';
import type { Surface } from './surface.js';
import { describe, type Action } from './surface.js';
import { packEntry, packCommands, previewPack, type PackEntry } from './feature-packs.js';

export async function featurePacks(ui: Surface, entry?: PackEntry) {
  const state = await ui.client.api('/v1/runtime-feature-packs/status');
  const packs = state.featurePacks || [];
  ui.open('Runtime 能力包', ['安装使用 server 包内既有事务 CLI；TUI 回读 Engine 的回执校验结果。'], [
    { label: '返回设置', run: () => ui.settings() },
    ...packs.map((pack: any) => ({ label: `${pack.productName || pack.id} · ${pack.status}`, run: () => packDetail(ui, pack.id, entry) })),
  ]);
}
async function packDetail(ui: Surface, packId: string, entry?: PackEntry) {
  const state = await ui.client.api('/v1/runtime-feature-packs/status');
  const pack = (state.featurePacks || []).find((item: any) => item.id === packId);
  if (!pack) throw new Error('Engine 已不再列出此能力包。');
  ui.open(pack.productName || pack.id, describe(pack), [
    { label: '返回能力包', run: () => featurePacks(ui, entry) },
    { label: '预览安装 / 修复', disabled: pack.installable !== true, run: async () => {
      let resolved = entry;
      if (!resolved) {
        try { resolved = await packEntry(); }
        catch (error: any) {
          ui.form('选择 server 安装目录', [{ key: 'path', label: '已安装 server 包的绝对目录', value: '' }], async values => {
            const selected = await packEntry(values.path.trim()); await showPackPreview(ui, packId, selected);
          }, [error.message, '使用原安装包的 v8os；不下载第二套安装器。']); return;
        }
      }
      await showPackPreview(ui, packId, resolved);
    } },
    { label: '回读安装结果 / 失败原因', run: () => packDetail(ui, packId, entry) },
  ]);
}
async function showPackPreview(ui: Surface, packId: string, entry: PackEntry) {
  const instanceId = ui.client.instance.instanceId;
  const plan = await previewPack(entry, packId);
  if (ui.client.instance.instanceId !== instanceId) throw new Error('实例已切换，请在当前实例重新预览。');
  const commands = packCommands(entry, packId);
  ui.confirm('能力包安装预览', [...describe(plan), `状态目录：${entry.stateRoot}`, `预览命令：\n${commands.preview}`, '确认后显示在服务器终端执行的精确命令；安装不会由 TUI 自动启动。'], '显示安装与恢复命令', async () => {
    ui.open('执行能力包安装', [
      '在服务器的另一终端执行：', commands.install, '', '回读完整事务状态：', commands.status,
      '安装入口使用已有 journal/receipt。失败时先查看下方回读的 lastError/logRef，修复原因后再运行同一安装命令。',
      '只有 Engine 回读为 installed 且回执有效才代表已安装；restartRequired 表示还需按服务流程重启。',
    ], [{ label: '回读安装结果 / 失败原因', run: () => packDetail(ui, packId, entry) }, { label: '返回能力包', run: () => featurePacks(ui, entry) }]);
  });
}

export async function plugins(ui: Surface) {
  const [catalog, installed, jobs] = await Promise.all([
    ui.client.api('/v1/api/plugins/catalog'), ui.client.api('/v1/api/plugins/installed'), ui.client.api('/v1/api/plugins/install-jobs?limit=50'),
  ]);
  const entries = catalog.items || catalog.plugins || [];
  ui.open('插件安装与管理', ['安装不等于授权；工具权限仍由 Engine 的任务授权控制。', ...describe(installed)], [
    { label: '返回 MCP / 插件', run: () => ui.extensions() },
    ...entries.map((plugin: any) => ({ label: `预览 ${plugin.name || plugin.displayName || plugin.id}`, run: () => previewPlugin(ui, plugin.id) })),
    ...((jobs.items || []).filter((job: any) => !job.dryRun).map((job: any) => ({ label: `安装记录 ${job.pluginId} · ${job.state}`, run: () => pluginJob(ui, job.jobId) }))),
  ]);
}
export async function previewPlugin(ui: Surface, pluginId: string) {
  const view = ui.client.view;
  const plan = await ui.client.api(`/v1/api/plugins/${encodeURIComponent(pluginId)}/install`, { method: 'POST', body: { dryRun: true } });
  if (!plan.planDigest || !plan.jobId || !plan.dryRun) throw new Error('Engine 未返回完整 dry-run 计划。');
  if (!plan.plan?.installable) {
    ui.open('插件当前不可安装', describe(plan.plan?.componentPolicy || plan), [{ label: '返回插件', run: () => plugins(ui) }]); return;
  }
  const idempotencyKey = randomUUID(); let submitted = false;
  ui.confirm('插件安装预览', describe(plan.plan), '批准此计划并安装', async () => {
    if (view !== ui.client.view) throw new Error('实例已变化，请在当前实例重新预览插件。');
    if (submitted) throw new Error('此计划已经提交，请从安装记录回读结果。');
    submitted = true;
    try {
      const job = await ui.client.api(`/v1/api/plugins/${encodeURIComponent(pluginId)}/install`, { method: 'POST', body: { dryRun: false, approved: true, planDigest: plan.planDigest, idempotencyKey } });
      if (!job.jobId) throw new Error('安装响应缺少事务标识');
      await pluginJob(ui, job.jobId);
    } catch (error: any) {
      if (error.staleView) throw error;
      ui.open('安装结果待确认', ['不会自动重发安装请求。请从 Engine 的安装记录回读事务状态。', error.message], [
        { label: '回读安装记录', run: () => plugins(ui) }, { label: '返回设置', run: () => ui.settings() },
      ]);
    }
  });
}
export async function pluginJob(ui: Surface, jobId: string) {
  const job = await ui.client.api(`/v1/api/plugins/install-jobs/${encodeURIComponent(jobId)}`);
  const state = job.state;
  const confirmed = state === 'ready' && job.result?.ok === true && Boolean(job.result?.receipt?.manifestDigest);
  const pending = !['ready', 'completed', 'failed', 'rolled_back', 'rollback_failed', 'external_reconciliation_required'].includes(state);
  const actions: Action[] = [{ label: '刷新事务与回执', run: () => pluginJob(ui, jobId) }, { label: '返回插件', run: () => plugins(ui) }];
  if (['failed', 'rolled_back'].includes(state)) actions.push({ label: '重新预览修复计划', run: () => previewPlugin(ui, job.pluginId) });
  if (['rollback_failed', 'external_reconciliation_required'].includes(state)) actions.push({ label: '检查插件当前状态', run: () => ui.readPage('插件诊断', `/v1/api/plugins/${encodeURIComponent(job.pluginId)}/readiness`) });
  ui.open('插件安装事务', [
    confirmed ? `Engine 已提交安装回执；健康状态：${job.result.state}` : pending ? 'Engine 正在处理，退出终端不取消后台安装。' : `尚未确认完整安装：${state}`,
    ...describe({ state, progress: job.progress, result: job.result, error: job.error, steps: job.steps }),
    ...(['rollback_failed', 'external_reconciliation_required'].includes(state) ? ['部分外部动作需要核对；先检查诊断和安装步骤，不能把重新提交当回滚。'] : []),
  ], actions);
}
