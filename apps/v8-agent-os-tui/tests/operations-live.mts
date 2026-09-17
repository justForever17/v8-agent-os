import assert from 'node:assert/strict';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { previewPlugin, pluginJob } from '../src/extension-pages.js';
import { createPeerInvitation } from '../src/peer-pages.js';
if (!process.argv.includes('--live') || !process.env.V8_AGENT_OS_HOME) throw new Error('--live and isolated state required');
const client = new Client(); await client.initialize(); assert.equal(client.connection, '已连接');
const ui = new Surface(client);
try {
  const catalog = await client.api('/v1/api/plugins/catalog');
  if (process.argv.includes('--inventory')) {
    console.log(JSON.stringify(catalog.plugins.map((plugin: any) => ({ id: plugin.id, cli: plugin.cliProfiles?.length, skills: plugin.skills?.length, mcp: plugin.mcpServers?.length, adapters: plugin.providerAdapters?.length }))));
  } else {
    await previewPlugin(ui, 'github'); assert.ok(['插件安装预览', '插件当前不可安装'].includes(ui.page!.title));
    const preview = ui.page!.title;
    createPeerInvitation(ui); await ui.page!.onSave!({ localRole: 'primary', localNickname: 'TUI fixture' });
    assert.equal(ui.page!.title, 'Peer 邀请已创建');
    assert.ok(!ui.page!.lines.some(line => line.includes('"code":')));
    const session = client.sessions.find(session => session.workspacePath);
    if (session) { await client.attach(session.id); assert.equal(client.workspace, session.workspacePath); assert.doesNotMatch(client.notice, /新建对话/); }
    let pluginReceipt = false, pluginUninstalled = false;
    if (process.argv.includes('--install-fixture-plugin')) {
      const plugin = catalog.plugins.find((plugin: any) => plugin.id === 'figma');
      assert.equal(plugin.cliProfiles.length, 0); assert.equal(plugin.skills.length, 0);
      const installed = await client.api('/v1/api/plugins/installed');
      assert.ok(!(installed.items || []).some((item: any) => item.pluginId === 'figma' || item.id === 'figma'), 'fixture requires absent plugin');
      try {
        await previewPlugin(ui, 'figma'); assert.equal(ui.page!.title, '插件安装预览');
        await ui.page!.actions[1].run();
        const deadline = Date.now() + 60000;
        let job: any;
        do {
          const jobs = await client.api('/v1/api/plugins/install-jobs?limit=50');
          job = jobs.items.find((job: any) => job.pluginId === 'figma' && !job.dryRun);
          if (job && ['ready', 'failed', 'rolled_back', 'rollback_failed', 'external_reconciliation_required'].includes(job.state)) break;
          await new Promise(resolve => setTimeout(resolve, 300));
        } while (Date.now() < deadline);
        assert.equal(job?.state, 'ready'); assert.ok(job.result?.receipt?.manifestDigest);
        await pluginJob(ui, job.jobId); assert.match(ui.page!.lines[0], /已提交安装回执/); pluginReceipt = true;
      } finally {
        await client.api('/v1/api/plugins/figma', { method: 'DELETE' });
        const result = await client.api('/v1/api/plugins/installed');
        pluginUninstalled = !(result.items || []).some((item: any) => (item.pluginId === 'figma' || item.id === 'figma') && item.state !== 'uninstalled');
        assert.ok(pluginUninstalled);
      }
    }
    console.log(JSON.stringify({ realEngine: true, pluginDryRun: preview, pluginReceipt, pluginUninstalled, peerInvitationCreated: true, sessionBindingReadback: Boolean(session), noProviderCall: true }));
  }
} finally { client.stop(); }
