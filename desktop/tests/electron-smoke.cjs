/* Real Electron + private Python service + local SSE. Never contacts a remote model. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const packaged = process.argv.includes('--packaged');
const directory = path.resolve(__dirname, '../temp', `smoke-${packaged ? 'package-' : ''}${Date.now()}`);
const workspace = path.join(directory, 'workspace');
fs.mkdirSync(workspace, { recursive: true });
let application;
const server = http.createServer((request, response) => {
  let body = '';
  request.on('data', chunk => { body += chunk; });
  request.on('end', () => {
    const latest = JSON.parse(body).messages.filter(message => message.role === 'user').map(message => String(message.content || '')).join('\n');
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const send = (delta, finish_reason = null) => response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta, finish_reason }] })}\n\n`);
    if (latest.includes('等待停止')) {
      send({ content: '这是一段仍在生成的内容。' });
      const timer = setInterval(() => send({ content: '继续…' }), 100);
      response.on('close', () => clearInterval(timer)); return;
    }
    if (latest.includes('授权命令')) send({ reasoning_content: '<tf-bash id="002" cmd="echo THINKFLOW_SMOKE" />' });
    else send({ reasoning_content: '<tf-write id="001" path="smoke.txt">本地桌面测试\n</tf-write>' });
    send({ content: '## 本地测试完成\n\n已写入 `smoke.txt`。\n\n**这是本地假端点返回的测试内容。**\n\n<script>alert("unsafe")</script>' });
    send({}, 'stop');
    response.write(`data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 120, completion_tokens: 40, total_tokens: 160 } })}\n\n`);
    response.end('data: [DONE]\n\n');
  });
});
async function state(page) { return page.evaluate(() => window.thinkflow.request('get_state')); }
async function waitIdle(page) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const current = await state(page);
    if (current.messages.length > 0 && !['running', 'approval'].includes(current.status)) return current;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('本地测试运行未在 20 秒内完成');
}
async function checkApprovalLayout(page) {
  const layout = await page.evaluate(() => {
    const panel = document.getElementById('approval'), allow = document.getElementById('approve'), reject = document.getElementById('reject'), params = document.getElementById('approval-params');
    return { panelBottom: panel.getBoundingClientRect().bottom, allowBottom: allow.getBoundingClientRect().bottom, rejectBottom: reject.getBoundingClientRect().bottom, panelClient: panel.clientHeight, panelScroll: panel.scrollHeight, params: params.textContent };
  });
  assert.equal(layout.allowBottom <= layout.panelBottom, true); assert.equal(layout.rejectBottom <= layout.panelBottom, true);
  assert.equal(layout.panelScroll <= layout.panelClient, true); assert.equal(layout.params.includes(': null'), false);
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const environment = { ...process.env, THINKFLOW_TEST_USER_DATA: path.join(directory, 'user-data') };
  delete environment.ELECTRON_RUN_AS_NODE;
  application = await electron.launch(packaged ? {
    executablePath: path.join(JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../artifacts/latest-desktop.json'), 'utf8')).path, 'ThinkFlow.exe'),
    args: [`--user-data-dir=${path.join(directory, 'user-data')}`], env: environment,
  } : { args: [path.resolve(__dirname, '..')], env: environment });
  const actual = await application.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath('userData') }));
  assert.equal(actual.packaged, packaged); assert.equal(path.resolve(actual.userData), path.join(directory, 'user-data'));
  const page = await application.firstWindow();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接', null, { timeout: 20000 });
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal((await state(page)).cwd, '');
  assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
  const preferences = await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
  assert.equal(preferences.contextIsolation, true); assert.equal(preferences.sandbox, true); assert.equal(preferences.nodeIntegration, false);
  assert.equal(await page.evaluate(async () => { try { await window.thinkflow.request('initialize', { data_dir: 'C:/forbidden' }); return false; } catch { return true; } }), true);
  await application.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, workspace);
  await page.locator('#setup-workspace').click();
  await page.waitForFunction(() => document.getElementById('workspace-name').textContent === 'workspace');
  await page.locator('#setup-model').click();
  await page.locator('[name=model]').fill('fake-local-model');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${port}`);
  await page.locator('[name=api_key]').fill('fake-smoke-key-only');
  await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  const config = (await state(page)).config;
  assert.equal(config.has_api_key, true); assert.equal(config.api_key, undefined);
  assert.equal(fs.readFileSync(path.join(directory, 'user-data', 'settings.json'), 'utf8').includes('fake-smoke-key-only'), false);
  if (process.argv.includes('--approval-only')) {
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1000, 700));
    await page.locator('#prompt').fill('授权命令'); await page.locator('#send').click();
    await page.locator('#approval').waitFor({ state: 'visible' }); await checkApprovalLayout(page);
    await page.screenshot({ path: path.join(directory, 'approval-1000.png') });
    console.log(JSON.stringify({ passed: true, evidence: directory, checks: '1000x700 approval footer remains visible; only parameters scroll; null fields removed' }, null, 2)); return;
  }
  await page.screenshot({ path: path.join(directory, 'welcome-1380.png') });
  await page.locator('#prompt').fill('测试写入'); await page.locator('#prompt').press('Control+Enter');
  const completed = await waitIdle(page);
  assert.equal(completed.last_error, '');
  assert.equal(fs.readFileSync(path.join(workspace, 'smoke.txt'), 'utf8'), '本地桌面测试\n');
  assert.equal(completed.ledger[0]?.status, 'success', JSON.stringify(completed));
  await page.waitForFunction(() => document.querySelectorAll('.ledger-entry').length === 1);
  assert.equal(await page.locator('#message-list script').count(), 0);
  await page.locator('#toggle-ledger').click();
  await page.locator('.message-copy').first().click();
  await page.waitForFunction(() => document.getElementById('toast').textContent === '已复制消息');
  await page.screenshot({ path: path.join(directory, 'conversation-1380.png') });
  await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1000, 700));
  await page.screenshot({ path: path.join(directory, 'conversation-1000.png') });
  const layout = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth, composer: document.getElementById('composer').getBoundingClientRect().toJSON(), inspector: document.getElementById('inspector').getBoundingClientRect().toJSON() }));
  assert.equal(layout.width >= layout.scrollWidth, true); assert.equal(layout.composer.right <= layout.inspector.left, true); assert.equal(layout.composer.bottom <= 700, true);
  await page.locator('#more-menu > summary').click();
  await page.locator('#fork').click();
  await page.waitForFunction(session => document.getElementById('session-caption').textContent !== `会话 · ${session.slice(0, 8)}`, completed.session_id);
  assert.equal((await state(page)).messages.length, completed.messages.length);
  await page.locator('#new-session').click(); await page.waitForFunction(() => document.querySelectorAll('.message').length === 0);
  await page.locator('#prompt').fill('授权命令'); await page.locator('#send').click();
  await page.locator('#approval').waitFor({ state: 'visible' });
  await checkApprovalLayout(page);
  await page.screenshot({ path: path.join(directory, 'approval-1000.png') });
  assert.match(await page.locator('#approval-params').textContent(), /THINKFLOW_SMOKE/);
  await page.locator('#reject').click(); await waitIdle(page);
  await page.locator('#new-session').click(); await page.waitForFunction(() => document.querySelectorAll('.message').length === 0);
  await page.locator('#prompt').fill('等待停止'); await page.locator('#send').click();
  await page.locator('#stop').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.getElementById('message-list').textContent.includes('仍在生成'));
  await page.locator('#stop').click();
  const cancelled = await waitIdle(page); assert.equal(cancelled.status, 'cancelled');
  const exported = path.join(directory, 'export.md');
  await application.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, exported);
  await page.locator('#more-menu > summary').click();
  await page.locator('#export').click();
  await page.waitForFunction(() => document.getElementById('toast').textContent === '会话已导出');
  assert.match(fs.readFileSync(exported, 'utf8'), /等待停止/);
  const workspaceData = path.join(directory, 'user-data', 'data', 'workspaces');
  const journal = fs.readdirSync(workspaceData, { recursive: true }).find(file => file.endsWith(`${cancelled.session_id}.jsonl`));
  const target = journal ? path.join(workspaceData, journal) : path.join(workspaceData, fs.readdirSync(workspaceData)[0], 'sessions', `${cancelled.session_id}.jsonl`);
  fs.appendFileSync(target, JSON.stringify({ type: 'tool_started', id: 'test-recovery', tool: 'write', path: 'check.txt', channel: 'text' }) + '\n');
  await page.locator('#new-session').click(); await page.waitForFunction(() => document.querySelectorAll('.message').length === 0);
  await page.locator(`[data-session-id="${cancelled.session_id}"]`).click();
  await page.locator('#recovery').waitFor({ state: 'visible' });
  await page.locator('#prompt').fill('继续任务'); assert.equal(await page.locator('#send').isDisabled(), true);
  await page.screenshot({ path: path.join(directory, 'recovery-1000.png') });
  await page.locator('#acknowledge-recovery').click(); await page.locator('#recovery').waitFor({ state: 'hidden' });
  assert.equal((await state(page)).recovery_required, false); assert.equal(fs.existsSync(path.join(workspace, 'check.txt')), false);
  await page.locator('#settings-top').click();
  await page.locator('[name=clear_api_key]').check(); await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  assert.equal((await state(page)).config.has_api_key, false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(directory, 'user-data', 'settings.json'), 'utf8')).encrypted_key, '');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, evidence: directory, checks: 'isolation, allowlist, first-run, encrypted settings, streaming write, safe Markdown, clipboard, 1000x700, fork, deny, cancel, export, recovery acknowledgement, explicit key clear' }, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (application) await application.close(); server.closeAllConnections(); server.close(); });
