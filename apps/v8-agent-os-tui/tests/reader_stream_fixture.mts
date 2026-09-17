/** Actual start()/reader lifecycle; replace only the network source with a
 * synthetic canonical stream. This is not an Engine/provider live test. */
import { Client } from '../src/client.js';
import { start } from '../src/main.js';

const pause = () => new Promise(resolve => setTimeout(resolve, 80));
Client.prototype.initialize = async function () {
  this.instance = { instanceId: 'reader-fixture', initialized: true };
  this.connection = '已连接'; this.view.sessionId = 'a'; this.changed();
};
Client.prototype.runLoop = async function () {
  let content = '只读一次的开头。';
  const publish = (state: string) => { this.messages = [{ id: 'm', role: 'assistant', state, content }]; this.changed(); };
  publish('streaming'); await pause();
  for (const delta of ['增量甲', '中文👩🏽‍💻', '\n\n', '末尾']) {
    content += delta; publish('streaming'); await pause();
  }
  publish('completed'); await pause();
  content = '修订后的文本'; publish('completed'); await pause();
  // A new session with an identical message id must start a fresh reader view.
  this.view.sessionId = 'b'; content = '新会话同ID'; publish('completed');
  process.stdout.write('\nREADER_FIXTURE_DONE\n');
};
await start(['--screen-reader']);
