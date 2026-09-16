import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { fetchJson } from '../src/http.mjs';

test('CLI Engine calls never forward service credentials through redirects', async () => {
  let destinationCalls = 0;
  const destination = http.createServer((req, res) => { destinationCalls++; res.end('{}'); });
  destination.listen(0, '127.0.0.1');
  await once(destination, 'listening');
  const origin = http.createServer((req, res) => {
    assert.equal(req.headers['x-v8-agent-os-secret'], 'fixture-service');
    res.writeHead(307, {Location: `http://127.0.0.1:${destination.address().port}/capture`});
    res.end();
  });
  origin.listen(0, '127.0.0.1');
  await once(origin, 'listening');
  try {
    await assert.rejects(fetchJson(`http://127.0.0.1:${origin.address().port}/identity`, {headers:{'x-v8-agent-os-secret':'fixture-service'}}));
    assert.equal(destinationCalls, 0);
  } finally {
    origin.closeAllConnections(); destination.closeAllConnections();
    await Promise.all([new Promise(r=>origin.close(r)), new Promise(r=>destination.close(r))]);
  }
});
