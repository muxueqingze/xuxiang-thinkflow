/* Real renderer, private service and loopback SSE. Source or --packaged, isolated data only. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const packaged = process.argv.includes('--packaged');
const directory = path.join(root, 'desktop/temp', `ux-${packaged ? 'package' : 'source'}-${Date.now()}`);
const userData = path.join(directory, 'profile');
const workspace = path.join(directory, '项目甲');
const workspaceB = path.join(directory, '项目乙');
for (const folder of [workspace, workspaceB]) fs.mkdirSync(folder, { recursive: true });
const requests = [], held = [], checks = [], pageErrors = [];
let application, page;
const server = http.createServer((request, response) => {
  let body = '';
  request.on('data', chunk => { body += chunk; });
  request.on('end', () => {
    const data = JSON.parse(body);
    // The harness appends a transient runtime-status user message to each API request.
    const prompt = data.messages.slice(0, -1).filter(message => message.role === 'user').at(-1)?.content || '';
    requests.push(prompt);
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const send = content => response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`);
    const finish = () => {
      response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })}\n\n`);
      response.end('data: [DONE]\n\n');
    };
    if (prompt.startsWith('持续任务')) {
      send('正在梳理项目的入口与执行边界。\n\n');
      const fail = () => { response.write(`data: ${JSON.stringify({ type: 'error', error: { message: '本地测试端点模拟失败' } })}\n\n`); response.end(); };
      held.push({ send, finish, fail, response });
    } else {
      send('## 交互验证记录\n\n这段回复来自本地测试端点。\n\n已核对会话导航与连续输入。\n\n```js\nconst ready = true;\n```\n\n' + Array.from({ length: 20 }, (_, i) => `第 ${i + 1} 项：保留用户输入，记录执行结果。`).join('\n\n'));
      finish();
    }
  });
});
async function eventually(predicate, description, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error(`Timeout: ${description}`);
}
const getState = () => page.evaluate(() => window.thinkflow.request('get_state'));
async function idle(count = 0) {
  await eventually(async () => { const state = await getState(); return !['running', 'approval'].includes(state.status) && state.messages.length >= count; }, 'idle');
  await page.waitForFunction(() => !document.getElementById('stop').offsetParent);
  return getState();
}
async function launch() {
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: userData }; delete env.ELECTRON_RUN_AS_NODE;
  const options = packaged ? {
    executablePath: path.join(JSON.parse(fs.readFileSync(path.join(root, 'artifacts/latest-desktop.json'), 'utf8')).path, 'ThinkFlow.exe'),
    args: [`--user-data-dir=${userData}`], env,
  } : { args: [path.join(root, 'desktop')], env };
  application = await electron.launch(options);
  const actual = await application.evaluate(({ app }) => ({ userData: app.getPath('userData'), packaged: app.isPackaged }));
  assert.equal(path.resolve(actual.userData), userData); assert.equal(actual.packaged, packaged);
  page = await application.firstWindow(); page.on('pageerror', error => pageErrors.push(error.message));
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
}
async function choose(folder) {
  await application.evaluate(({ dialog }, target) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [target] }); }, folder);
  await page.locator('#workspace').click();
  await eventually(async () => (await getState()).cwd === folder, 'workspace changed');
}
async function submit(text) { await page.locator('#prompt').fill(text); await page.locator('#prompt').press('Enter'); }
async function menu(id) { if (!(await page.locator('#more-menu').evaluate(node => node.open))) await page.locator('#more-menu > summary').click(); await page.locator(id).click(); }
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await launch();
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal(await page.locator('#inspector').isVisible(), false);
  await choose(workspace);
  await page.locator('#settings-top').click();
  await page.locator('[name=model]').fill('本地交互测试');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);
  await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  await page.locator('#prompt').fill('输入法候选');
  await page.locator('#prompt').evaluate(node => {
    node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
    node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
  });
  assert.equal(requests.length, 0);
  await page.locator('#prompt').press('Shift+Enter');
  assert.match(await page.locator('#prompt').inputValue(), /\n/);
  await submit('第一条交互验收'); await idle(2);
  checks.push('Enter sends, Shift+Enter newline, composition Enter does not send');
  await page.locator('#message-list').evaluate(node => { window.__firstMessage = node.firstElementChild; });
  const firstSession = (await getState()).session_id;
  await submit('持续任务：检查队列');
  await eventually(() => held.length === 1, 'first held request');
  await submit('排队的补充说明');
  await page.locator('#queue-panel').waitFor({ state: 'visible' });
  assert.equal(requests.length, 2);
  held[0].finish(); await idle(6);
  assert.equal(requests.at(-1), '排队的补充说明');
  assert.equal(await page.locator('#message-list').evaluate(node => window.__firstMessage === node.firstElementChild), true);
  checks.push('FIFO follow-up after successful run; existing message DOM preserved');
  await submit('持续任务：停止后暂停'); await eventually(() => held.length === 2, 'second held request');
  await submit('等待手动继续'); const beforeStop = requests.length;
  await page.locator('#stop').click(); await idle(8);
  assert.equal((await getState()).status, 'cancelled');
  await page.waitForTimeout(350); assert.equal(requests.length, beforeStop);
  await page.locator('#resume-queue').click(); await idle(10);
  assert.equal(requests.at(-1), '等待手动继续');
  checks.push('Cancel pauses queued follow-up until explicit resume');
  await page.locator('#prompt').fill('项目甲会话草稿');
  await page.keyboard.press('Control+n');
  await eventually(async () => (await getState()).session_id !== firstSession, 'new session');
  assert.equal(await page.locator('#prompt').inputValue(), '');
  await page.locator(`[data-session-id="${firstSession}"]`).click();
  await page.waitForFunction(() => document.getElementById('prompt').value === '项目甲会话草稿');
  await menu('#rename-current'); await page.locator('#rename-input').fill('桌面交互验收');
  await page.locator('#rename-form').evaluate(form => form.requestSubmit());
  await page.waitForFunction(() => document.getElementById('session-title').textContent === '桌面交互验收');
  await menu('#pin-current'); await eventually(async () => (await getState()).sessions.find(item => item.id === firstSession).pinned, 'pin');
  await menu('#archive-current'); await eventually(async () => (await getState()).sessions.find(item => item.id === firstSession).archived, 'archive');
  assert.equal(await page.locator('#send').isDisabled(), true);
  await menu('#archive-current'); await eventually(async () => !(await getState()).sessions.find(item => item.id === firstSession).archived, 'restore archive');
  checks.push('Session draft isolation, rename, pin, reversible archive');
  await page.keyboard.press('Control+k');
  await page.locator('#command-dialog').waitFor({ state: 'visible' });
  await page.locator('#command-search').fill('桌面交互验收');
  assert.match(await page.locator('#command-results').innerText(), /桌面交互验收/);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+b'); assert.equal(await page.locator('.sidebar').isVisible(), false);
  await page.keyboard.press('Control+b'); assert.equal(await page.locator('.sidebar').isVisible(), true);
  checks.push('Command search and sidebar keyboard shortcuts');
  await choose(workspaceB); assert.equal(await page.locator('#prompt').inputValue(), '');
  await page.locator('#recent-workspaces').click();
  await page.locator('#recent-list button').filter({ hasText: '项目甲' }).click();
  await eventually(async () => (await getState()).session_id === firstSession, 'recent workspace restores session');
  await page.waitForFunction(() => document.getElementById('prompt').value === '项目甲会话草稿');
  const rejected = await page.evaluate(async () => { try { await window.thinkflow.openRecentWorkspace('C:/unregistered'); return false; } catch { return true; } });
  assert.equal(rejected, true);
  checks.push('Recent project restores correct session and draft; arbitrary path rejected');
  await submit('持续任务：重启恢复'); await eventually(() => held.length === 3, 'third held request');
  await submit('重启后需要确认发送');
  await page.locator('#prompt').fill('尚未发送的草稿');
  const requestCount = requests.length;
  await application.close(); application = null;
  await launch();
  assert.equal((await getState()).session_id, firstSession);
  await page.waitForFunction(() => document.getElementById('prompt').value === '尚未发送的草稿');
  await page.locator('#queue-panel').waitFor({ state: 'visible' });
  await page.waitForTimeout(350); assert.equal(requests.length, requestCount);
  await page.locator('#resume-queue').click(); await idle(14);
  assert.equal(requests.at(-1), '重启后需要确认发送');
  checks.push('Restart restores project/session/draft and leaves queue paused');
  await page.locator('#messages-scroll').evaluate(node => { node.scrollTop = 0; });
  await submit('持续任务：阅读位置'); await eventually(() => held.length === 4, 'fourth held request');
  await page.locator('#messages-scroll').evaluate(node => { node.scrollTop = 0; });
  held[3].send('新的流式段落。'.repeat(100));
  await page.waitForTimeout(250);
  assert.ok(await page.locator('#messages-scroll').evaluate(node => node.scrollTop < 20));
  await page.locator('#back-to-bottom').click();
  held[3].finish(); await idle(16);
  checks.push('Reading older messages does not follow stream; jump-to-latest works');
  await page.screenshot({ path: path.join(directory, 'conversation-1380.png') });
  await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1000, 700));
  await page.locator('#toggle-ledger').click();
  const layout = await page.evaluate(() => {
    const rect = id => document.getElementById(id).getBoundingClientRect().toJSON();
    return { viewport: [innerWidth, innerHeight], scroll: [document.documentElement.scrollWidth, document.documentElement.scrollHeight], composer: rect('composer'), inspector: rect('inspector') };
  });
  assert.ok(layout.scroll[0] <= layout.viewport[0]); assert.ok(layout.composer.bottom <= layout.viewport[1]);
  assert.ok(layout.composer.right <= layout.inspector.left + 1);
  await page.screenshot({ path: path.join(directory, 'conversation-1000.png') });
  await page.keyboard.press('Control+,');
  const settings = await page.evaluate(() => {
    const dialog = document.getElementById('settings-dialog'), save = document.getElementById('save-settings');
    return { bottom: save.getBoundingClientRect().bottom, height: innerHeight, client: dialog.clientHeight, scroll: dialog.scrollHeight };
  });
  assert.ok(settings.bottom <= settings.height); assert.ok(settings.scroll <= settings.client + 1);
  await page.screenshot({ path: path.join(directory, 'settings-1000.png') });
  await page.keyboard.press('Escape');
  checks.push('1000×700: composer/ledger do not overlap; settings actions visible with one scroll area');
  await submit('持续任务：失败后暂停'); await eventually(() => held.length === 5, 'failure request');
  await submit('失败后取回编辑'); const beforeFailure = requests.length;
  held[4].fail(); await idle(18);
  assert.equal((await getState()).status, 'error');
  await page.waitForTimeout(350); assert.equal(requests.length, beforeFailure);
  const originalQueued = (await getState()).input_queue.find(item => item.status !== 'running');
  await page.locator('.queue-edit').click();
  await page.locator('#queue-edit-dialog').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#queue-edit-prompt').inputValue(), '失败后取回编辑');
  await page.locator('#queue-edit-prompt').fill('失败后已编辑的后续任务');
  await page.locator('#save-queue-edit').click();
  await page.locator('#queue-edit-dialog').waitFor({ state: 'hidden' });
  const edited = (await getState()).input_queue.find(item => item.id === originalQueued.id);
  assert.equal(edited.prompt, '失败后已编辑的后续任务');
  assert.equal(edited.id, originalQueued.id);
  assert.equal(requests.length, beforeFailure);
  assert.equal(await page.locator('#prompt').inputValue(), '');
  await page.locator('#resume-queue').click(); await idle(20);
  assert.equal(requests.at(-1), '失败后已编辑的后续任务');
  checks.push('Provider failure pauses authoritative queue; edit retains identity and waits for explicit resume');
  // Discard one accepted receipt at the renderer boundary. The controller must
  // query the exact command ID from the real service, never create a new input.
  await page.evaluate(() => {
    const original = window.acceptInputReceipt;
    let dropped = false;
    window.acceptInputReceipt = async (...args) => {
      window.__receiptCommand = args[2];
      if (!dropped) { dropped = true; return false; }
      window.acceptInputReceipt = original;
      return original(...args);
    };
  });
  await submit('回执丢失模拟'); await idle(22);
  await page.locator('#input-receipt-panel').waitFor({ state: 'hidden' });
  const receiptId = await page.evaluate(() => window.__receiptCommand);
  const receipt = await page.evaluate(async command_id => window.thinkflow.request('get_input_receipt', { command_id, session_id: state.session_id }), receiptId);
  assert.equal(receipt.receipt.id, receiptId);
  assert.equal(receipt.receipt.status, 'completed');
  const acceptedCount = requests.length;
  await page.reload();
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
  await page.waitForTimeout(350); assert.equal(requests.length, acceptedCount);
  assert.equal(await page.locator('#input-receipt-panel').isVisible(), false);
  await page.locator('#prompt').fill('回执核对后的新草稿');
  assert.equal(await page.locator('#send').isEnabled(), true);
  checks.push('Lost receipt is resolved by exact command identity; reload does not replay and input remains usable');
  assert.deepEqual(pageErrors, []);
  const evidence = { passed: true, packaged, checks, layout, settings, pageErrors, requestCount: requests.length };
  fs.writeFileSync(path.join(directory, 'results.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ ...evidence, evidence: directory }, null, 2));
}
main().catch(async error => {
  console.error(error); process.exitCode = 1;
  fs.writeFileSync(path.join(directory, 'failure.json'), JSON.stringify({ error: error.message, requests, checks, pageErrors }, null, 2));
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(directory, 'failure.png') }).catch(() => {});
}).finally(async () => { if (application) await application.close(); server.closeAllConnections(); server.close(); });
