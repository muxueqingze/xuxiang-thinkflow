/* Explicit real-provider smoke, never part of npm test or CI. */
if (!process.argv.includes('--live')) { console.log('Use --live to authorize this finite DeepSeek test.'); process.exit(0); }
const { _electron: electron } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..'), packaged = process.argv.includes('--packaged');
const directory = path.join(root, 'desktop/temp', `live-${packaged?'package':'source'}-${Date.now()}`);
const profile = path.join(directory, 'profile'), workspace = path.join(directory, 'workspace');
fs.mkdirSync(workspace, { recursive: true });
let application, page; const errors=[];
async function main(){
  let key=execFileSync(process.env.THINKFLOW_PYTHON || 'python',['-B','-c','from scripts.live_deepseek import load_key; print(load_key(),end="")'],{cwd:root,encoding:'utf8',windowsHide:true,stdio:['ignore','pipe','pipe']});
  const env={...process.env,THINKFLOW_TEST_USER_DATA:profile};delete env.ELECTRON_RUN_AS_NODE;delete env.DEEPSEEK_API_KEY;
  const options=packaged?{executablePath:path.join(JSON.parse(fs.readFileSync(path.join(root,'artifacts/latest-desktop.json'))).path,'ThinkFlow.exe'),args:[`--user-data-dir=${profile}`],env}:{args:[path.join(root,'desktop')],env};
  application=await electron.launch(options);page=await application.firstWindow();page.on('pageerror',e=>errors.push(e.message));
  const runtime=await application.evaluate(({app})=>({profile:app.getPath('userData'),version:app.getVersion(),packaged:app.isPackaged}));assert.equal(path.resolve(runtime.profile),profile);
  await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
  await application.evaluate(({dialog},workspace)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[workspace]});},workspace);await page.locator('#workspace').click();
  await page.evaluate(config=>window.thinkflow.request('configure',config),{provider:'openai',base_url:'https://api.deepseek.com',api_path:'',model:'deepseek-flash',api_key:key,max_tokens:4096,max_run_turns:10,max_run_seconds:180,security_profile:'balanced',thinking_mode:'enabled',reasoning_effort:'high'});
  const saved=fs.readFileSync(path.join(profile,'settings.json'),'utf8');assert.equal(saved.includes(key),false);key='';
  await page.reload();await page.waitForFunction(()=>document.getElementById('model-label').textContent==='deepseek-flash');
  await page.evaluate(()=>{window.__liveTimings=[];window.thinkflow.onEvent(event=>{if(['stream_started','stream_finished','tool_completed'].includes(event.type))window.__liveTimings.push({type:event.type,turn:event.turn,id:event.id,tool:event.tool,flow:event.flow,status:event.status,at:performance.now()});});});
  await page.locator('#prompt').fill('这是隔离桌面验收。不要联网、不要读取skill。创建calc.py，提供add(a,b)正确相加；创建说明.md，用中文解释它。两个文件都用tf-write连续输出。随后用bash仅运行 python -c "from calc import add; assert add(2,3)==5; print(\'CHECK_OK\')" 验证。等待真实执行结果后简短报告。');
  const started=Date.now();await page.locator('#send').click();
  let state, approvals=0;
  while(Date.now()-started<180000){
    state=await page.evaluate(()=>window.thinkflow.request('get_state'));
    if(state.pending_approval){
      const command=state.pending_approval.input?.cmd || '';
      assert.equal(state.pending_approval.tool,'bash');
      assert.ok(command.includes('from calc import add')&&command.includes('CHECK_OK'));
      approvals++;await page.locator('#approve').click();
    }
    if(state.messages.length && !['running','approval'].includes(state.status) && !state.input_queue.some(x=>x.status==='queued'))break;
    await new Promise(r=>setTimeout(r,180));
  }
  assert.equal(state.status,'idle');assert.equal(state.last_error,'');assert.ok(approvals>0);
  assert.ok(fs.readFileSync(path.join(workspace,'calc.py'),'utf8').includes('a + b'));assert.ok(fs.statSync(path.join(workspace,'说明.md')).size>0);
  assert.ok(state.ledger.some(r=>r.tool==='bash'&&r.status==='success'&&r.exit_code===0&&r.output_summary.includes('CHECK_OK')));
  assert.ok(state.usage.reported_turns > 0, 'At least one completed stream must report usage; early feedback barriers can close before provider usage.');
  assert.ok(state.ledger.filter(r=>['write','edit'].includes(r.tool)&&r.status==='success'&&r.flow==='delayed').length>=2, 'Predictable file operations must use the streaming route by default.');
  const events=await page.evaluate(()=>window.__liveTimings);
  const writesBeforeStreamClosed=events.filter(e=>e.type==='tool_completed'&&e.flow==='delayed'&&e.status==='success'&&events.some(end=>end.type==='stream_finished'&&end.turn===e.turn&&end.at>e.at)).length;
  const report={passed:true,packaged,version:runtime.version,model:state.config.model,seconds:(Date.now()-started)/1000,approvals,writesBeforeStreamClosed,events,usage:state.usage,ledger:state.ledger.map(({id,tool,status,flow,exit_code})=>({id,tool,status,flow,exit_code})),errors};
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify(report,null,2));
  await page.locator('#toggle-ledger').click();await page.locator('#tab-changes').click();await page.locator('.change-entry').first().click();await page.bringToFront();await page.screenshot({path:path.join(directory,'live-result.png')});
  console.log(JSON.stringify({directory,...report}));
}
main().catch(error=>{console.error('Live desktop check failed:',error.name,error.message?.replace(/(?:sk-)[A-Za-z0-9_-]+/g,'[REDACTED]').slice(0,800));process.exitCode=1;}).finally(async()=>{if(application)await application.close();});
