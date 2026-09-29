const { test } = require('node:test');
const assert = require('node:assert/strict');
const { BackendClient } = require('../backend.cjs');
test('private stdio handles split NDJSON, events, RPC errors and shutdown', async () => {
  const source = `process.stdin.setEncoding('utf8'); let buffer=''; process.stdin.on('data',chunk=>{buffer+=chunk;let end;while((end=buffer.indexOf('\\n'))>=0){let request=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);if(request.method==='shutdown'){process.stdout.write(JSON.stringify({id:request.id,result:{closed:true}})+'\\n');process.exit(0);}else if(request.method==='bad'){process.stdout.write(JSON.stringify({id:request.id,error:{message:'known error'}})+'\\n');}else{const response=JSON.stringify({id:request.id,result:request.params})+'\\n';process.stdout.write(response.slice(0,5));process.stdout.write(response.slice(5));process.stdout.write(JSON.stringify({event:{type:'state'}})+'\\n');}}});`;
  const client = new BackendClient(process.execPath, ['-e', source]);
  const events = []; client.on('event', event => events.push(event));
  assert.deepEqual(await client.request('echo', { text: '中文' }), { text: '中文' });
  await assert.rejects(client.request('bad'), /known error/);
  assert.equal(events[0].type, 'state');
  await client.shutdown();
});
test('backend exit rejects outstanding requests without leaking stderr', async () => {
  const client = new BackendClient(process.execPath, ['-e', `process.stderr.write('fake-private-key');process.exit(2);`]);
  let event; client.on('event', payload => { event = payload; });
  await assert.rejects(client.request('wait'), /后端/);
  assert.equal(JSON.stringify(event).includes('fake-private-key'), false);
});
