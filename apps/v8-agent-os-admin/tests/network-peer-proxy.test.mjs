import assert from 'node:assert/strict';
import test from 'node:test';
import { proxyNetworkPeer } from '../src/lib/server/network-peer-proxy.ts';
const request = (body='{}', headers={}) => new Request('https://node.example/v1/network-supervisor/peer/neighbors/messages', {
    method:'POST', headers:{'content-type':'application/json', 'x-v8-peer-token':'fixture-peer', cookie:'fixture-admin', ...headers}, body,
});
test('catchall forwards signed bytes and peer token to exact Engine path without Admin secrets', async()=>{
    const body='{"signature":"fixture","payload":{"text":"hello"}}';
    const reply=await proxyNetworkPeer(request(body),['peer','neighbors','messages'],'http://127.0.0.1:9530/v1',async(url,init)=>{
        assert.equal(url,'http://127.0.0.1:9530/v1/network-supervisor/peer/neighbors/messages');
        assert.equal(Buffer.from(init.body).toString(),body);
        assert.equal(init.headers.get('x-v8-peer-token'),'fixture-peer');
        assert.equal(init.headers.get('cookie'),null);
        assert.equal(init.redirect,'error'); assert.ok(init.signal);
        return Response.json({detail:'invalid signature'},{status:403});
    });
    assert.equal(reply.status,403); assert.deepEqual(await reply.json(),{detail:'invalid signature'});
});
test('pairing requires Engine code/signature but not an existing peer token',async()=>{
    const result=await proxyNetworkPeer(request('{}',{'x-v8-peer-token':''}),['peer','neighbors','pairing','consume'],'http://engine/v1',async(_url,init)=>{
        assert.equal(init.headers.get('x-v8-peer-token'),null); return Response.json({detail:'invalid code'},{status:403});
    });
    assert.equal(result.status,403);
});
test('unapproved peers, administration, traversal and WS never reach Engine',async()=>{
    for(const path of [['peer','secrets'],['peer','arbitrary'],['neighbors','links'],['peer','..','config'],['peer','neighbors/messages']]){
        assert.equal((await proxyNetworkPeer(request(),path,'http://engine/v1',()=>assert.fail('unexpected fetch'))).status,404);
    }
    assert.equal((await proxyNetworkPeer(request(),['peer','ws'],'http://engine/v1',()=>assert.fail())).status,426);
});
test('runtime wake, delegation and callbacks use the same token-preserving exact POST ingress', async()=>{
    for (const path of ['wake','delegations']) {
        const response=await proxyNetworkPeer(request(),['peer',path],'http://engine/v1',async(url,init)=>{
            assert.equal(url,`http://engine/v1/network-supervisor/peer/${path}`);
            assert.equal(init.headers.get('x-v8-peer-token'),'fixture-peer');
            assert.equal(init.headers.get('authorization'),null);
            return Response.json({detail:'unknown peer'},{status:403});
        });
        assert.equal(response.status,403);
    }
});
test('byte limit applies without content-length and upstream failure is redacted',async()=>{
    assert.equal((await proxyNetworkPeer(request('x'.repeat(262145)),['peer','challenge'],'http://engine/v1',()=>assert.fail())).status,413);
    const reply=await proxyNetworkPeer(request(),['peer','challenge'],'http://engine/v1',()=>{throw Error('private-token');});
    assert.equal(reply.status,502); assert.doesNotMatch(await reply.text(),/private-token/);
});
