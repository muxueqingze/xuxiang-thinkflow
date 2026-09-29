/* v0.8 complete product workflow against a real private backend and local SSE. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..'), packaged = process.argv.includes('--packaged');
const directory = path.join(root, 'desktop/temp', `workbench-${packaged?'package':'source'}-${Date.now()}`);
const profile = path.join(directory, 'profile'), workspace = path.join(directory, 'workspace');
fs.mkdirSync(workspace, { recursive: true }); fs.writeFileSync(path.join(workspace, 'notes.txt'), '原有说明\n');
const checks=[], errors=[], requests=[], held=[];
let application, page;
const server = http.createServer((request,response) => {
  let raw=''; request.on('data',chunk=>raw+=chunk); request.on('end',()=>{
    if(request.method==='GET'){response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({data:[{id:'fixture-model'}]}));return;}
    const body=JSON.parse(raw);
    if(!body.stream){response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({model:'fixture-model',choices:[{message:{content:'OK'}}],usage:{total_tokens:10}}));return;}
    const user=body.messages.slice(0,-1).filter(m=>m.role==='user' && !String(m.content).startsWith('[THINKFLOW')).at(-1)?.content || '';
    requests.push(user); response.writeHead(200,{'content-type':'text/event-stream'});
    const send=delta=>response.write(`data: ${JSON.stringify({choices:[{index:0,delta,finish_reason:null}]})}\n\n`);
    const finish=(reason='stop')=>{response.write(`data: ${JSON.stringify({choices:[{index:0,delta:{},finish_reason:reason}]})}\n\n`);response.end('data: [DONE]\n\n');};
    if(user.startsWith('保持运行')) {send({content:'正在持续执行，后续任务可排队。'});held.push({send,finish,response});return;}
    if(user.startsWith('更新说明') && !body.messages.some(m=>m.role==='tool'&&m.tool_call_id==='plan-fixture')){
      send({content:'<tf-write id="1" path="notes.txt">更新后的说明\n</tf-write>\n文件已更新，继续核对任务。'});
      send({tool_calls:[{index:0,id:'plan-fixture',type:'function',function:{name:'update_plan',arguments:JSON.stringify({explanation:'说明文件更新',steps:[{id:'docs',title:'更新说明',acceptance:'保留文档结构并写入新说明',status:'completed',evidence:['1']}]})}}]});finish('tool_calls');return;
    }
    send({content:'任务完成。\n\n变更与执行回执可在工作台查看。'});finish();
  });
});
async function until(fn,label,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,80));}throw new Error('Timeout: '+label);}
const state=()=>page.evaluate(()=>window.thinkflow.request('get_state'));
async function launch(){
  const env={...process.env,THINKFLOW_TEST_USER_DATA:profile};delete env.ELECTRON_RUN_AS_NODE;
  const opts=packaged?{executablePath:path.join(JSON.parse(fs.readFileSync(path.join(root,'artifacts/latest-desktop.json'))).path,'ThinkFlow.exe'),args:[`--user-data-dir=${profile}`],env}:{args:[path.join(root,'desktop')],env};
  application=await electron.launch(opts);page=await application.firstWindow();page.on('pageerror',e=>errors.push(e.message));
  assert.equal(path.resolve(await application.evaluate(({app})=>app.getPath('userData'))),profile);
  await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
}
async function submit(value){await page.locator('#prompt').fill(value);await page.locator('#prompt').press('Enter');}
async function idle(){await until(async()=>!['running','approval'].includes((await state()).status),'idle');await page.waitForFunction(()=>document.getElementById('stop').hidden);}
async function choose(){await application.evaluate(({dialog},workspace)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[workspace]});},workspace);await page.locator('#workspace').click();await until(async()=>(await state()).cwd===workspace,'workspace');}
async function main(){
  await new Promise(r=>server.listen(0,'127.0.0.1',r));await launch();await choose();
  await page.locator('#settings-top').click();await page.locator('[name=model]').fill('fixture-model');await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);await page.locator('#save-settings').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  await page.locator('#settings-top').click();await page.locator('#model-probe').click();await page.waitForFunction(()=>document.getElementById('model-probe-result').textContent.includes('连接测试通过'));
  await page.locator('#model-catalog').click();await page.locator('.model-option').waitFor();assert.equal(await page.locator('.model-option').first().textContent(),'fixture-model');await page.locator('#close-settings').click();checks.push('Saved endpoint connection probe and model catalog');
  await page.locator('#attach-file').click();await page.locator('.file-entry[data-path="notes.txt"]').click();await page.locator('#preview-file-content').waitFor();assert.match(await page.locator('#preview-file-content').textContent(),/原有说明/);await page.locator('#attach-preview-file').click();await page.locator('#attachment-chips').waitFor();
  await submit('更新说明，使用附加文件的原版本。');await until(()=>requests.length>=2,'write and plan requests');await idle();assert.equal(fs.readFileSync(path.join(workspace,'notes.txt'),'utf8'),'更新后的说明\n');
  const completed=await state();assert.equal(completed.task_plan.steps[0].status,'completed');assert.equal(completed.ledger.find(x=>x.id==='1').status,'success');checks.push('File preview, revision-bound attachment, streaming write and evidence-backed plan');
  await page.locator('#tab-tasks').click();assert.match(await page.locator('#panel-tasks').textContent(),/保留文档结构/);await page.screenshot({path:path.join(directory,'tasks.png')});
  await page.locator('#tab-changes').click();await page.locator('.change-entry').first().click();await until(async()=>(await page.locator('#change-diff').textContent()).includes('-原有说明'),'diff');assert.equal(await page.locator('#open-revert').isEnabled(),true);await page.screenshot({path:path.join(directory,'diff.png')});
  fs.writeFileSync(path.join(workspace,'notes.txt'),'外部编辑必须保留\n');await page.locator('#open-revert').click();await page.locator('#confirm-revert').click();await until(()=>page.locator('#revert-error').isVisible(),'revert conflict');assert.equal(fs.readFileSync(path.join(workspace,'notes.txt'),'utf8'),'外部编辑必须保留\n');
  await page.locator('#cancel-revert').click();fs.writeFileSync(path.join(workspace,'notes.txt'),'更新后的说明\n');await page.locator('#open-revert').click();await page.locator('#confirm-revert').click();await page.waitForFunction(()=>!document.getElementById('revert-dialog').open);assert.equal(fs.readFileSync(path.join(workspace,'notes.txt'),'utf8'),'原有说明\n');checks.push('Diff, conflict refusal and explicit version-checked restore');
  await page.locator('#close-ledger').click();
  await application.evaluate(({ipcMain})=>{const channel='thinkflow:request',original=ipcMain._invokeHandlers.get(channel);let drop=true;ipcMain.removeHandler(channel);ipcMain.handle(channel,async(event,method,params)=>{const result=await original(event,method,params);if(drop&&method==='submit_input'){drop=false;return {ok:false,error:'fixture lost ACK after durable acceptance'};}return result;});});
  await submit('保持运行：检查精确回执');await until(()=>held.length===1,'hold');await page.waitForFunction(()=>document.getElementById('input-receipt-panel').hidden);const before=requests.length;await submit('后续待编辑任务');await page.locator('.queue-edit:not(:disabled)').first().click();await page.locator('#queue-edit-prompt').fill('编辑后的后续任务');await page.locator('#save-queue-edit').click();await page.waitForFunction(()=>!document.getElementById('queue-edit-dialog').open);await page.locator('#stop').click();await idle();assert.equal(requests.length,before);assert.equal((await state()).queue_paused,true);checks.push('Lost ACK queried without replay; backend queue edit; stop pauses continuation');
  await application.close();application=null;await launch();await page.waitForTimeout(200);assert.equal(requests.length,before);assert.equal((await state()).queue_paused,true);assert.match(await page.locator('#queue-list').textContent(),/编辑后的后续任务/);await page.locator('#resume-queue').click();await until(()=>requests.length===before+1,'resume');await idle();assert.equal(requests.at(-1),'编辑后的后续任务');checks.push('Restart keeps durable queue paused; explicit resume executes edited task once');
  await page.locator('#attach-file').click();await page.locator('.file-entry[data-path="notes.txt"]').click();await application.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1000,700));await page.waitForTimeout(100);await page.screenshot({path:path.join(directory,'files-1000.png')});
  const geometry=await page.evaluate(()=>({width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth,composer:document.getElementById('composer').getBoundingClientRect().toJSON(),inspector:document.getElementById('inspector').getBoundingClientRect().toJSON()}));assert.ok(geometry.scroll<=geometry.width);assert.ok(geometry.composer.bottom<=geometry.height);checks.push('1000×700 file workspace fits viewport');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify({passed:true,packaged,checks,errors,geometry,modelRequests:requests.length},null,2));console.log(JSON.stringify({directory,passed:true,checks,errors}));
}
main().catch(async error=>{console.error(error);if(page)await page.screenshot({path:path.join(directory,'failure.png')}).catch(()=>{});process.exitCode=1;}).finally(async()=>{if(application)await application.close();server.closeAllConnections();server.close();});
