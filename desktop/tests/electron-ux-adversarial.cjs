const { _electron: electron } = require('playwright');
const http = require('node:http'), fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '../..');
const assert = require('node:assert/strict');
const packaged = process.argv.includes('--packaged');
const stressOnly = process.argv.includes('--stress-only');
const ackOnly = process.argv.includes('--ack-only');
const fixture = path.join(root, 'desktop/temp', `ux-adversarial-${packaged ? 'package' : 'source'}-${Date.now()}`);
const profile = path.join(fixture, 'profile'), workA = path.join(fixture, 'workspace-a'), workB = path.join(fixture, 'workspace-b');
for (const dir of [workA, workB]) fs.mkdirSync(dir, { recursive: true });
const requests = [], holds = [], checks = [], problems = [], errors = [];
let app, page;
const server = http.createServer((req, res) => {
  let body = ''; req.on('data', b => body += b); req.on('end', () => {
    const data = JSON.parse(body);
    const prompt = data.messages.filter(m => m.role === 'user' && typeof m.content === 'string' && !m.content.includes('THINKFLOW') && !m.content.startsWith('\n\n当前')).at(-1)?.content || '';
    requests.push(prompt);
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const packet = delta => res.write(`data: ${JSON.stringify({ choices: [{ delta, finish_reason: null }] })}\n\n`);
    const finish = (reason = 'stop') => { res.write(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: reason }] })}\n\n`); res.end('data: [DONE]\n\n'); };
    if (prompt === '独立授权压力' && !data.messages.some(m => m.role === 'tool')) {
      packet({ tool_calls: [{ index: 0, id: 'approval-stress', type: 'function', function: { name: 'bash', arguments: JSON.stringify({ cmd: 'echo ' + '本地授权参数请核对 '.repeat(160) }) } }] });
      finish('tool_calls');
    } else if (prompt.startsWith('独立保持')) {
      packet({ content: '正在等待本地测试信号。\n\n' });
      holds.push({ packet, finish, fail: () => { res.write('data: {"type":"error","error":{"message":"独立模拟失败"}}\n\n'); res.end(); } });
    } else {
      packet({ content: '## 续想交互验收\n\n这是隔离测试端点返回的回复。\n\n' + Array.from({ length: 10 }, (_,i) => `段落 ${i + 1}：会话、草稿与待发输入都应该保留清楚的归属。`).join('\n\n') });
      finish();
    }
  });
});
async function until(fn, label) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) { if (await fn()) return; await new Promise(r => setTimeout(r, 60)); }
  throw new Error('Timeout: ' + label);
}
const getState = () => page.evaluate(() => window.thinkflow.request('get_state'));
const idle = () => until(async () => !['running','approval'].includes((await getState()).status), 'idle');
async function launch() {
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: profile }; delete env.ELECTRON_RUN_AS_NODE;
  const options = packaged ? {
    executablePath: path.join(JSON.parse(fs.readFileSync(path.join(root, 'artifacts/latest-desktop.json'), 'utf8')).path, 'ThinkFlow.exe'),
    args: [`--user-data-dir=${profile}`], env,
  } : { args: [path.join(root, 'desktop')], env };
  app = await electron.launch(options);
  const actual = await app.evaluate(({ app }) => app.getPath('userData'));
  assert.equal(path.resolve(actual), profile, 'Isolation failure');
  assert.equal(await app.evaluate(({ app }) => app.isPackaged), packaged);
  page = await app.firstWindow(); page.on('pageerror', e => errors.push(e.message));
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
}
async function choose(target) {
  await app.evaluate(({ dialog }, target) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [target] }); }, target);
  await page.locator('#workspace').click();
  await until(async () => (await getState()).cwd === target, 'workspace');
}
async function submit(text) { await page.locator('#prompt').fill(text); await page.locator('#prompt').press('Enter'); }
async function capture(name) {
  const metrics = await page.evaluate(() => {
    const out = { viewport: [innerWidth, innerHeight], scrollWidth: document.documentElement.scrollWidth, elements: {} };
    for (const id of ['approval','approve','reject','composer','prompt','send','stop','queue-panel','messages-scroll','execution-summary','inspector']) {
      const el = document.getElementById(id), r = el.getBoundingClientRect();
      out.elements[id] = { x:r.x, y:r.y, width:r.width, height:r.height, right:r.right, bottom:r.bottom, visible:!!el.offsetParent };
    }
    return out;
  });
  await page.screenshot({ path: path.join(fixture, name + '.png') });
  fs.writeFileSync(path.join(fixture, name + '.json'), JSON.stringify(metrics,null,2));
  return metrics;
}
// Inject a lost IPC reply only after the real private backend accepts/persists run.
// This is test-only Electron instrumentation; no production API is replaced.
async function ackScenario() {
  await submit('独立保持第一条'); await until(() => holds.length === 1, 'first run');
  await submit('独立保持回执丢失任务'); await page.locator('#queue-panel').waitFor({state:'visible'});
  await app.evaluate(({ ipcMain }) => {
    const key = 'thinkflow:request', original = ipcMain._invokeHandlers.get(key);
    let drop = true; ipcMain.removeHandler(key);
    ipcMain.handle(key, async (event, method, params) => {
      const result = await original(event, method, params);
      if (method === 'run' && drop) { drop = false; return {ok:false,error:'Local fixture: startup receipt lost after acceptance'}; }
      return result;
    });
  });
  holds[0].finish(); await until(() => requests.length === 2, 'second run accepted');
  await page.waitForFunction(() => document.getElementById('queue-list').textContent.includes('发送状态待核对'));
  assert.equal((await getState()).status, 'running');
  await capture('lost-start-receipt');
  await page.locator('#stop').click(); await idle();
  // A disabled button is the preferred UI; clicking an enabled one must still be inert.
  if (!(await page.locator('#resume-queue').isDisabled())) await page.locator('#resume-queue').click();
  await page.waitForTimeout(200); assert.equal(requests.length, 2);
  await app.close(); app = null; await launch(); await page.waitForTimeout(200);
  assert.match(await page.locator('#queue-list').innerText(), /发送状态待核对/);
  if (!(await page.locator('#resume-queue').isDisabled())) await page.locator('#resume-queue').click();
  await page.waitForTimeout(200); assert.equal(requests.length, 2);
  await capture('restart-uncertain');
  const result = {passed:true,packaged,requests:requests.length,checks:['accepted request survives lost receipt','cancel keeps uncertain work paused','restart never replays uncertain work'],errors};
  fs.writeFileSync(path.join(fixture,'ack-results.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({fixture,...result},null,2)); assert.deepEqual(errors,[]);
}

async function main() {
  await new Promise(r => server.listen(0, '127.0.0.1', r)); await launch(); await choose(workA);
  await page.locator('#settings-top').click(); await page.locator('[name=model]').fill('本地独立质检');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`); await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  if (ackOnly) return ackScenario();
  await page.locator('#prompt').fill('输入法测试');
  await page.locator('#prompt').evaluate(el => { el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,isComposing:true})); el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})); });
  await page.locator('#prompt').press('Shift+Enter');
  checks.push({ name:'IME/Shift', pass:requests.length === 0 && (await page.locator('#prompt').inputValue()).includes('\n') });
  await submit('独立正常对话'); await idle();
  await capture('conversation-1380x900');
  await submit('独立授权压力'); await until(async () => (await getState()).status === 'approval', 'approval');
  for (let i=0;i<6;i++) await submit(`等待中的后续任务 ${i + 1}：${'请保留这条内容。'.repeat(20)}`);
  const originalDraft = Array.from({length:16},(_,i)=>`尚未发送的草稿第${i+1}行：${'长文本 '.repeat(15)}`).join('\n');
  await page.locator('#prompt').fill(originalDraft);
  await page.locator('#toggle-ledger').click();
  const largeStress = await capture('stress-1380x900');
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1000,700));
  await page.waitForTimeout(150);
  const stress = await capture('stress-1000x700');
  for (const measured of [largeStress,stress]) for (const id of ['composer','approve','reject','send','stop']) {
    const e=measured.elements[id]; if (e.bottom>measured.viewport[1] || e.right>measured.viewport[0] || e.y<0) problems.push({ name:'offscreen',id,rect:e,viewport:measured.viewport });
  }
  if (stressOnly) { fs.writeFileSync(path.join(fixture,'stress-results.json'),JSON.stringify({largeStress,stress,problems,errors},null,2)); console.log(JSON.stringify({fixture,problems,errors},null,2)); assert.deepEqual(problems,[]); assert.deepEqual(errors,[]); return; }
  const beforeQueue = await page.locator('#queue-list > li').count();
  await page.locator('.queue-edit').first().click();
  checks.push({ name:'edit does not overwrite existing draft', pass:(await page.locator('#prompt').inputValue())===originalDraft && (await page.locator('#queue-list > li').count())===beforeQueue });
  await capture('draft-preserved-1000x700');
  await page.locator('#prompt').fill(''); await page.locator('.queue-edit').first().click();
  checks.push({ name:'edit empty composer retrieves and pauses', pass:(await page.locator('#prompt').inputValue()).startsWith('等待中的后续任务 1') && (await page.locator('#queue-list > li').count())===beforeQueue-1 });
  await page.locator('#reject').click(); await idle();
  const countAfterReject=requests.length; await page.waitForTimeout(250);
  checks.push({ name:'denied approval leaves queue paused',pass:requests.length===countAfterReject });
  // A real run with immediate error must preserve queued items and current draft.
  await page.locator('#prompt').fill('工作区甲隔离草稿');
  const first=(await getState()).session_id;
  await page.keyboard.press('Control+n'); await until(async()=>(await getState()).session_id!==first,'new session');
  const second=(await getState()).session_id;
  await page.locator('#prompt').fill('新会话草稿');
  await page.locator(`[data-session-id="${first}"]`).click();
  await page.waitForFunction(()=>document.getElementById('prompt').value==='工作区甲隔离草稿');
  checks.push({name:'session draft restored',pass:true});
  // Rapid real UI switch attempts: the busy gate must leave backend and title aligned.
  await page.evaluate(({first,second})=>{document.querySelector(`[data-session-id="${second}"]`).click();document.querySelector(`[data-session-id="${first}"]`).click();},{first,second});
  await until(async()=>!(await page.locator('#new-session').isDisabled()),'switch settles');
  const current=await getState(); const uiActive=await page.locator('.session-button[aria-current="true"]').getAttribute('data-session-id');
  checks.push({name:'rapid switch state matches backend',pass:current.session_id===uiActive});
  await choose(workB); checks.push({name:'workspace draft isolation',pass:(await page.locator('#prompt').inputValue())===''});
  await page.locator('#prompt').fill('工作区乙草稿');
  await page.locator('#recent-workspaces').click(); await page.locator('#recent-list button').filter({hasText:'workspace-a'}).click();
  await until(async()=>(await getState()).cwd===workA,'back to A');
  checks.push({name:'recent workspace preserves selected session draft',pass:(await page.locator('#prompt').inputValue())==='新会话草稿'});
  const callsBeforeRestart=requests.length; await app.close(); app=null; await launch(); await page.waitForTimeout(250);
  checks.push({name:'restart sends nothing',pass:requests.length===callsBeforeRestart});
  await page.locator(`[data-session-id="${first}"]`).click(); await until(async()=>(await getState()).session_id===first,'return queue');
  checks.push({name:'restart retains paused queue',pass:(await page.locator('#queue-list > li').count())===5 && (await page.locator('#queue-status').innerText()).includes('已暂停')});
  await capture('restart-1380x900');
  fs.writeFileSync(path.join(fixture,'results.json'),JSON.stringify({checks,problems,errors,requests},null,2));
  console.log(JSON.stringify({fixture,checks,problems,errors},null,2));
  assert.ok(checks.every(item => item.pass)); assert.deepEqual(problems,[]); assert.deepEqual(errors,[]);
}
main().catch(async e=>{ console.error(e); if(page) await capture('failure').catch(()=>{}); process.exitCode=1; }).finally(async()=>{if(app) await app.close();server.closeAllConnections();server.close();});
