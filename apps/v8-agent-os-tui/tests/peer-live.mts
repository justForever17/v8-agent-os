import assert from 'node:assert/strict';
import { readFile, writeFile, chmod } from 'node:fs/promises';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { editor } from '../src/terminal.js';
import { createPeerInvitation, consumePeerInvitation } from '../src/peer-pages.js';
if (!process.argv.includes('--live') || !process.env.V8_AGENT_OS_HOME) throw new Error('--live and isolated state required');
const role = process.argv[process.argv.indexOf('--role') + 1];
const file = process.argv[process.argv.indexOf('--invitation-file') + 1];
const client = new Client(); await client.initialize(); assert.equal(client.connection, '已连接');
const ui = new Surface(client);
try {
  if (role === 'create') {
    createPeerInvitation(ui); await ui.page!.onSave!({ localRole: 'primary', localNickname: 'TUI Primary' });
    assert.equal(ui.page!.title, 'Peer 邀请已创建'); await ui.page!.actions[1].run();
    const invitation = ui.page!.lines.find(line => line.startsWith('{'));
    assert.ok(invitation); await writeFile(file, invitation, { mode: 0o600, flag: 'wx' }); await chmod(file, 0o600);
    await ui.close(true);
  } else {
    const invitation = await readFile(file, 'utf8');
    consumePeerInvitation(ui); ui.formEditor = editor(invitation); await ui.dispatch({ key: 'f9' });
    assert.equal(ui.page!.title, '确认 Peer 身份'); assert.equal(ui.page!.selected, 0);
    await ui.page!.actions[1].run(); assert.match(client.notice, /已回读到受信任/);
    assert.equal(client.draft.text, '');
    client.save(true);
    const ownView = await readFile(client.store.file, 'utf8'); assert.ok(!ownView.includes(JSON.parse(invitation).code));
  }
  console.log(JSON.stringify({ role, actualEngine: true, surfaceCompleted: true }));
} finally { client.stop(); }
