const http = require('node:http');
const model = 'zcode-local-demo';
function completionChunk(delta, finish = null, usage) {
  return { id: 'chatcmpl-local-reference', object: 'chat.completion.chunk', created: Math.floor(Date.now()/1000), model,
    choices: usage ? [] : [{index: 0, delta, finish_reason: finish}], ...(usage ? {usage} : {}) };
}
const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url.split('?')[0].endsWith('/models')) {
    res.writeHead(200, {'content-type':'application/json'});
    return res.end(JSON.stringify({object:'list',data:[{id:model,object:'model',created:0,owned_by:'local'}]}));
  }
  if (req.method !== 'POST' || !req.url.split('?')[0].endsWith('/chat/completions')) {
    res.writeHead(404); return res.end();
  }
  let body = ''; for await (const buf of req) body += buf; const payload = JSON.parse(body);
  const names = payload.tools?.map(({function: f}) => f?.name) ?? [];
  const name = names.find(n => /(?:^|[_.:-])bash$/i.test(n ?? ''));
  const lastUser = payload.messages?.findLast(m => m.role === 'user');
  const priorToolResult = payload.messages?.slice(payload.messages.lastIndexOf(lastUser)+1).some(m => m.role === 'tool');
  const tool = name && !priorToolResult && /命令|terminal|command/i.test(JSON.stringify(lastUser?.content));
  const usage = {prompt_tokens:1445,completion_tokens:64,total_tokens:1509};
  const reply = '本机源码对照会话已就绪。这里可以检查模型与权限菜单、上下文占用、会话历史和工作区面板。';
  const fn = tool ? {name,arguments:JSON.stringify({command:"node -e \"require('node:fs').writeFileSync('zcode-reference-approval.txt','fixture')\"",description:'显示本机验证文本'})} : undefined;
  if (!payload.stream) {
    const message = {role:'assistant',content:tool?null:reply,...(tool?{tool_calls:[{id:'call-local-reference',type:'function',function:fn}]}:{})};
    res.writeHead(200,{'content-type':'application/json'}); return res.end(JSON.stringify({id:'chatcmpl-local-reference',object:'chat.completion',created:Math.floor(Date.now()/1000),model,choices:[{index:0,message,finish_reason:tool?'tool_calls':'stop'}],usage}));
  }
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache',connection:'keep-alive'});
  const write = data => res.write('data: '+JSON.stringify(data)+'\n\n');
  write(completionChunk({role:'assistant'}));
  if (tool) write(completionChunk({tool_calls:[{index:0,id:'call-local-reference',type:'function',function:fn}]}));
  else for (const part of reply.match(/.{1,10}/gu)) {
    await new Promise(resolve => setTimeout(resolve, 55)); write(completionChunk({content:part}));
  }
  write(completionChunk({},tool?'tool_calls':'stop'));
  if (payload.stream_options?.include_usage) write(completionChunk({},null,usage));
  res.end('data: [DONE]\n\n');
});
server.listen(18764,'127.0.0.1',()=>console.log('ZCode local fixture http://127.0.0.1:18764/v1'));
