const {_electron:electron} = require('playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const root=path.resolve(__dirname,'../..'), directory=path.join(root,'temp',`desktop-composer-${Date.now()}`), workspace=path.join(directory,'workspace');
fs.mkdirSync(workspace,{recursive:true});
let app,page,releaseCommand;const errors=[],checks=[];let modelRequests=0,catalogRequests=0;
const server=http.createServer((request,response)=>{let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
  if(request.method==='GET'){catalogRequests++;response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({data:[{id:'fixture-a'},{id:'fixture-b'}]}));return;}
  modelRequests++;response.writeHead(200,{'content-type':'text/event-stream'});
  const send=content=>response.write(`data: ${JSON.stringify({choices:[{index:0,delta:{content},finish_reason:null}]})}\n\n`);
  const end=()=>{response.write(`data: ${JSON.stringify({choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1234,completion_tokens:56,total_tokens:1290}})}\n\n`);response.end('data: [DONE]\n\n');};
  if(modelRequests===1){send('检查工作区后运行命令。');releaseCommand=()=>{send('<tf-bash id="1" cmd="echo THINKFLOW_PERMISSION_OK" />');end();};}
  else{send('命令已完成，退出码为 0。');end();}
});});
const rpc=(method,params={})=>page.evaluate(({method,params})=>window.thinkflow.request(method,params),{method,params});
async function until(fn,label){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(60);}throw new Error('Timeout: '+label);}
async function shot(name){await page.waitForTimeout(280);await page.screenshot({path:path.join(directory,`${name}.png`),scale:'css'});}
async function main(){
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const env={...process.env,THINKFLOW_TEST_USER_DATA:path.join(directory,'profile')};delete env.ELECTRON_RUN_AS_NODE;
  app=await electron.launch({args:[path.join(root,'desktop')],env});page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
  await app.evaluate(({dialog},cwd)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[cwd]});},workspace);await page.locator('#workspace').click();
  await page.locator('#settings-side').click();await page.locator('[name=model]').fill('fixture-a');await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);await page.locator('#settings-nav-advanced').click();await page.locator('#runtime-config').fill('{"compaction":{"max_input_tokens":20000}}');await page.locator('#save-settings').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  await page.locator('#policy-button').click();assert.equal(await page.locator('#settings-dialog').isVisible(),false);await shot('permissions');
  await page.locator('[data-policy-choice=read-only]').click();await until(async()=>(await rpc('get_state')).config.security_profile==='read-only','quick policy save');
  assert.equal(await page.locator('#prompt').evaluate(node=>document.activeElement===node),true);
  await page.locator('#policy-button').click();await page.locator('[data-policy-choice=balanced]').click();await until(async()=>(await rpc('get_state')).config.security_profile==='balanced','balanced');
  assert.equal((await rpc('get_state')).runtime_config.compaction.max_input_tokens,20000);checks.push('Permission popover updates mode in place, keeps runtime overrides and returns to draft');
  await page.locator('#settings-top').click();assert.equal(catalogRequests,0);assert.equal(modelRequests,0);await page.locator('#refresh-model-picker').click();await page.locator('#model-picker-list .choice-row').filter({hasText:'fixture-b'}).click();await until(async()=>(await rpc('get_state')).config.model==='fixture-b','model selection');
  await page.locator('#settings-top').click();await page.locator('#model-picker-search').fill('fixture-custom');await page.locator('#model-picker-search').press('Enter');await until(async()=>(await rpc('get_state')).config.model==='fixture-custom','custom model');
  assert.equal(modelRequests,0);assert.equal(catalogRequests,1);checks.push('Model picker, explicit catalog retrieval and manual name use no model calls');
  await page.locator('#settings-top').click();await shot('models');await page.keyboard.press('Escape');
  await page.locator('#prompt').fill('执行授权命令，完成后说明结果。');await page.locator('#prompt').press('Enter');await until(()=>Boolean(releaseCommand),'waiting local stream');
  await page.locator('#prompt').fill('这份草稿要原样保留。');assert.equal(await page.locator('#stop').isVisible(),false);assert.equal(await page.locator('#send').isVisible(),true);releaseCommand();
  await page.locator('#approval').waitFor();assert.equal(await page.locator('#composer').isVisible(),false);assert.equal(await page.locator('#execution-summary').isVisible(),false);assert.equal(await page.locator('#approval-command').textContent(),'echo THINKFLOW_PERMISSION_OK');
  assert.equal(await page.locator('#prompt').inputValue(),'这份草稿要原样保留。');assert.equal(modelRequests,1);await shot('approval');
  await page.locator('#approve').click();await until(async()=>!['running','approval'].includes((await rpc('get_state')).status),'finish');
  assert.equal(await page.locator('#composer').isVisible(),true);assert.equal(await page.locator('#prompt').inputValue(),'这份草稿要原样保留。');assert.equal((await rpc('get_state')).ledger.find(item=>item.id==='1').exit_code,0);checks.push('Approval shows actual command in the input dock, preserves draft and executes after consent');
  await page.locator('#context-label').hover();await until(()=>page.locator('#context-popover').isVisible(),'context hover');assert.match(await page.locator('#context-budget').textContent(),/20,000/);assert.match(await page.locator('#context-estimate-label').textContent(),/最近一次输入/);assert.ok((await rpc('get_state')).context.last_input_tokens_estimate>0);await shot('context');
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1000,700));await page.locator('#policy-button').click();await shot('permissions-1000');
  const menu=await page.locator('#policy-popover').boundingBox();const size=await page.evaluate(()=>({width:innerWidth,height:innerHeight}));assert.ok(menu.x>=0&&menu.y>=0&&menu.x+menu.width<=size.width&&menu.y+menu.height<=size.height);checks.push('Context hover uses last actual request estimate and a separately named compaction threshold; small menus fit 1000×700');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify({passed:true,checks,errors,modelRequests,catalogRequests},null,2));console.log(JSON.stringify({directory,passed:true,checks,modelRequests,catalogRequests}));
}
main().catch(async error=>{console.error(error);if(page)await shot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{if(app)await app.close();server.closeAllConnections();server.close();});
