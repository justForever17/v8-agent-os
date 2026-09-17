import assert from 'node:assert/strict';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { previewPlugin } from '../src/extension-pages.js';
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
    console.log(JSON.stringify({ realEngine: true, pluginDryRun: preview, peerInvitationCreated: true, sessionBindingReadback: Boolean(session), noProviderCall: true }));
  }
} finally { client.stop(); }
