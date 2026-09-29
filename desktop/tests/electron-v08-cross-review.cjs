// Source Electron only. Isolated profile/workspace and localhost SSE; no real provider.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const { _electron: electron } = require(path.join(root,'desktop/node_modules/playwright'));
const directory = path.join(root, 'desktop/temp', `v08-cross-review-${Date.now()}`), profile = path.join(directory,'profile'), workspace = path.join(directory,'workspace');
fs.mkdirSync(workspace,{recursive:true});
fs.writeFileSync(path.join(workspace,'input.txt'),'original attachment\n');
fs.writeFileSync(path.join(workspace,'other.txt'),'another attachment\n');
let app, page, requests=0;const errors=[], checks=[], findings=[];
async function until(check) { const deadline=Date.now()+15000; while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,80))}throw Error('fixture condition timed out') }
const server = http.createServer((request,response)=>{
  let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{
    requests++;const data=JSON.parse(body);response.writeHead(200,{'content-type':'text/event-stream'});
    const has=id=>data.messages.some(message=>message.role==='tool'&&message.tool_call_id===id);
    let delta={content:'本地隔离回复。'},finish='stop';
    if(data.messages.some(message=>message.role==='user'&&String(message.content).includes('独立验收文件变更'))){
      let id,name,args;
      if(!has('qa-read')){id='qa-read';name='read';args={path:'input.txt'}}
      else if(!has('qa-write')){id='qa-write';name='write';args={path:'input.txt',content:'changed by isolated model fixture\n'}}
      else if(!has('qa-plan')){id='qa-plan';name='update_plan';args={explanation:'本地隔离验收',steps:[{id:'changed',title:'修改文件并核对回执',acceptance:'文件工具返回成功回执',status:'completed',evidence:['qa-write']}]}}
      if(id){delta={tool_calls:[{index:0,id,type:'function',function:{name,arguments:JSON.stringify(args)}}]};finish='tool_calls'}
    }
    response.end('data: '+JSON.stringify({choices:[{delta,finish_reason:finish}]})+'\n\ndata: [DONE]\n\n');
  });
});
async function snap(name,width,height){
  await app.evaluate(({BrowserWindow},size)=>BrowserWindow.getAllWindows()[0].setContentSize(...size),[width,height]);
  await page.waitForTimeout(120);
  await page.screenshot({path:path.join(directory,name+'.png')});
  const metrics=await page.evaluate(()=>({viewport:[innerWidth,innerHeight],scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,
    nodes:Object.fromEntries(['prompt','send','input-receipt-panel','restore-unreceived-input','inspector','composer-region'].map(id=>{const n=document.getElementById(id),r=n?.getBoundingClientRect();return [id,r?{x:r.x,y:r.y,w:r.width,h:r.height,bottom:r.bottom,visible:!!n.offsetParent}:null]}))}));
  fs.writeFileSync(path.join(directory,name+'.json'),JSON.stringify(metrics,null,2));return metrics;
}
async function run(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const env={...process.env,THINKFLOW_TEST_USER_DATA:profile};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.join(root,'desktop')],env});
 assert.equal(path.resolve(await app.evaluate(({app})=>app.getPath('userData'))),profile);
 page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));
 await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
 await app.evaluate(({dialog},folder)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[folder]})},workspace);
 await page.locator('#workspace').click();
 await page.locator('#settings-top').click();
 await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);
 await page.locator('[name=model]').fill('independent-local-fixture');
 await page.locator('#save-settings').click();
 await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
 await page.locator('#attach-file').click();
 await page.locator('[data-path="input.txt"]').click();
 await page.locator('#attach-preview-file').click();
 fs.writeFileSync(path.join(workspace,'input.txt'),'edited externally before submission\n');
 await page.locator('#prompt').fill('带旧版本附件的任务');await page.locator('#prompt').press('Enter');
 await page.waitForFunction(()=>document.getElementById('input-receipt-status').textContent.includes('未查到'));
 assert.equal(requests,0);assert.equal(await page.locator('#send').isDisabled(),true);
 await snap('stale-attachment-1380',1380,900);await snap('stale-attachment-1000',1000,700);
 await page.locator('#prompt').fill('用户刚写的新草稿');await page.locator('#restore-unreceived-input').click();
 assert.equal(await page.locator('#prompt').inputValue(),'用户刚写的新草稿');
 assert.equal(await page.locator('#input-receipt-panel').isVisible(),true);
 checks.push('stale attachment rejected without model request; existing text draft survives explicit restore');
 await page.locator('#prompt').fill('');await page.locator('#restore-unreceived-input').click();
 assert.equal(await page.locator('#prompt').inputValue(),'带旧版本附件的任务');
 assert.equal(await page.locator('#input-receipt-panel').isVisible(),false);
 checks.push('definitively unreceived candidate can be explicitly restored with its attachment');
 // Isolate a stale snapshot response after session switching; no fake backend state is persisted.
 const projection=await page.evaluate(async()=>{
   const before=await window.thinkflow.request('get_state');
   const after=await window.thinkflow.request('new_session');applyState(after,{navigation:true});
   const accepted=applyState(before);
   const rendered=state.session_id;applyState(after,{navigation:true});
   return {oldSeq:before.seq,newSeq:after.seq,acceptedOld:accepted!==false,renderedOld:rendered===before.session_id};
 });
  if(projection.renderedOld)findings.push({severity:'P2',kind:'equal-sequence-old-session-snapshot',...projection});
 await page.locator('#prompt').fill('独立验收文件变更');await page.locator('#prompt').press('Enter');
 await until(async()=>{const s=await page.evaluate(()=>window.thinkflow.request('get_state'));return s.ledger.some(item=>item.id==='qa-write'&&item.status==='success')&&s.task_plan?.steps?.length===1&&s.status==='idle'&&s.input_queue.length===0});
 const actualState=await page.evaluate(()=>window.thinkflow.request('get_state'));
 fs.writeFileSync(path.join(directory,'runtime-summary.json'),JSON.stringify({requests,status:actualState.status,last_error:actualState.last_error,ledger:actualState.ledger,input_queue:actualState.input_queue},null,2));
 console.log(JSON.stringify({phase:'file-run',requests,status:actualState.status,error:actualState.last_error,ledger:actualState.ledger.map(item=>({id:item.id,tool:item.tool,status:item.status,error:item.error}))}));
 assert.equal(fs.readFileSync(path.join(workspace,'input.txt'),'utf8'),'changed by isolated model fixture\n');
 await page.locator('#tab-changes').click();
 await page.locator('.change-entry').first().click();
 await page.waitForFunction(()=>!document.getElementById('open-revert').disabled);
 assert.match(await page.locator('#change-diff').innerText(),/changed by isolated model fixture/);
 await page.waitForFunction(()=>document.getElementById('toast').hidden);
 await snap('file-diff-1380',1380,900);await snap('file-diff-1000',1000,700);
 await page.locator('#open-revert').click();await page.locator('#confirm-revert').click();
 await page.waitForFunction(()=>!document.getElementById('revert-dialog').open);
 assert.equal(fs.readFileSync(path.join(workspace,'input.txt'),'utf8'),'edited externally before submission\n');
 await page.locator('#tab-tasks').click();
 assert.match(await page.locator('#task-plan-list').innerText(),/模型标记/);
 checks.push('real tool receipts feed diff and guarded UI revert through correct field names; task plan shows model-marked evidence');
 assert.deepEqual(findings, []); assert.deepEqual(errors, []);
 fs.writeFileSync(path.join(directory,'results.json'),JSON.stringify({checks,findings,errors,requests,source:true},null,2));
 console.log(JSON.stringify({directory,checks,findings,errors,requests}));
}
run().catch(error=>{console.error(error.stack);process.exitCode=1}).finally(async()=>{if(app)await app.close();await new Promise(resolve=>server.close(resolve))});
