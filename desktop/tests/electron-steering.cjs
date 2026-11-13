/* Renderer → IPC → private backend → loopback SSE, with an isolated hidden window. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const packaged = process.argv.includes('--packaged');
const outputRoot = path.join(root, 'temp', 'steering-ui-20261113');
fs.mkdirSync(outputRoot, { recursive: true });
const directory = fs.mkdtempSync(path.join(outputRoot, 'electron-'));
const workspace = path.join(directory, 'workspace');
const launcher = path.join(directory, 'launch.cjs');
const userData = path.join(directory, 'profile');
fs.mkdirSync(workspace);
const readResult = 'ONLY_FROM_STEERING_READ_FIXTURE';
fs.writeFileSync(path.join(workspace, 'notes.txt'), readResult);
fs.writeFileSync(launcher, `
  const { app, BrowserWindow } = require('electron');
  BrowserWindow.prototype.show = function () {};
  app.on('browser-window-created', (_event, window) => window.webContents.setBackgroundThrottling(false));
  require(${JSON.stringify(path.join(root, 'desktop', 'main.cjs'))});
`);
const prompts = {
  first: '先阅读工作区说明',
  steer: '请在当前任务中补充提炼关键点',
  followUp: '另开后续任务整理格式',
  shortcut: '另开后续任务整理标题',
};
const requests = [], errors = [];
let app, page, releaseFirst;
const server = http.createServer((request, response) => {
  let raw = '';
  request.on('data', chunk => raw += chunk);
  request.on('end', () => {
    requests.push(JSON.parse(raw).messages);
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const send = content => response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`);
    const finish = () => {
      response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })}\n\n`);
      response.end('data: [DONE]\n\n');
    };
    if (requests.length === 1) {
      send('我会先读取工作区说明。\n');
      releaseFirst = () => { send('<tf-read id="1" path="notes.txt" />'); finish(); };
    } else {
      send('已按当前任务的要求完成。');
      finish();
    }
  });
});
const state = () => page.evaluate(() => window.thinkflow.request('get_state'));
async function until(check, label) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(`Timeout: ${label}`);
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: userData };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch(packaged ? {
    executablePath: path.join(JSON.parse(fs.readFileSync(path.join(root, 'artifacts/latest-desktop.json'), 'utf8')).path, 'ThinkFlow.exe'),
    args: [`--user-data-dir=${userData}`], env,
  } : { args: [launcher], env });
  page = await app.firstWindow();
  if (packaged) await app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.prototype.show = function () {};
    for (const window of BrowserWindow.getAllWindows()) {
      window.hide(); window.webContents.setBackgroundThrottling(false);
    }
  });
  assert.equal(await app.evaluate(({ app }) => app.isPackaged), packaged);
  page.on('pageerror', error => errors.push(error.message));
  assert.equal(path.resolve(await app.evaluate(({ app }) => app.getPath('userData'))), userData);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false);
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
  await app.evaluate(({ dialog }, cwd) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [cwd] }); }, workspace);
  await page.locator('#workspace').click();
  await until(async () => (await state()).cwd === workspace, 'isolated workspace');
  await page.locator('#settings-side').click();
  await page.locator('[name=model]').fill('steering-fixture');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);
  await page.locator('#save-settings').click();
  await page.locator('#settings-dialog').waitFor({ state: 'hidden' });

  const prompt = page.locator('#prompt'), delivery = page.locator('#input-delivery');
  assert.equal(await delivery.isVisible(), false);
  await prompt.fill(prompts.first);
  await prompt.press('Enter');
  await until(() => releaseFirst, 'initial request');
  await delivery.waitFor();
  assert.equal(await delivery.inputValue(), 'steer');

  await prompt.fill('候选输入');
  await prompt.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })));
  await prompt.press('Alt+Enter');
  await prompt.press('Enter');
  await prompt.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })));
  await prompt.press('Shift+Enter');
  assert.match(await prompt.inputValue(), /\n/);
  assert.deepEqual((await state()).input_queue.filter(item => item.status !== 'running'), []);
  await prompt.evaluate(node => node.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter', altKey: true, bubbles: true, isComposing: true,
  })));
  assert.deepEqual((await state()).input_queue.filter(item => item.status !== 'running'), []);

  const queue = page.locator('#queue-list');
  await delivery.selectOption('follow_up');
  await prompt.fill(prompts.followUp);
  assert.equal(await page.locator('#send').getAttribute('aria-label'), '排队任务');
  await page.locator('#send').click();
  await queue.getByText(`排队任务 · ${prompts.followUp}`).waitFor();
  await page.waitForFunction(() => !pendingInput);
  assert.equal(await delivery.inputValue(), 'steer');

  await app.evaluate(({ ipcMain }) => {
    const key = 'thinkflow:request', original = ipcMain._invokeHandlers.get(key);
    process.steeringFixture = { submissions: 0, receiptQueries: 0 };
    ipcMain.removeHandler(key);
    ipcMain.handle(key, async (event, method, params) => {
      const result = await original(event, method, params);
      if (method === 'get_input_receipt') process.steeringFixture.receiptQueries++;
      if (method === 'submit_input' && params.delivery === 'steer' && ++process.steeringFixture.submissions === 1) {
        return { ok: false, error: 'fixture lost ACK after durable acceptance' };
      }
      return result;
    });
  });
  await prompt.fill(prompts.steer);
  await prompt.press('Enter');
  await queue.getByText(`补充当前 · ${prompts.steer}`).waitFor();
  await until(async () => (await app.evaluate(() => process.steeringFixture)).receiptQueries === 1, 'lost steering ACK queried');
  await page.waitForFunction(() => !pendingInput);
  await prompt.fill(prompts.shortcut);
  await prompt.press('Alt+Enter');
  await queue.getByText(`排队任务 · ${prompts.shortcut}`).waitFor();
  await page.waitForFunction(() => !pendingInput);

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1000, 700));
  await page.locator('#toggle-ledger').click();
  await page.locator('#inspector').waitFor();
  await page.waitForFunction(() => document.getElementById('workspace-upper').getAnimations().length === 0);
  await prompt.fill('待编辑草稿');
  const layout = await page.evaluate(() => {
    const ids = ['attach-file', 'policy-button', 'context-label', 'settings-top', 'thinking-button', 'input-delivery', 'send'];
    const composer = document.getElementById('composer').getBoundingClientRect();
    const controls = ids.map(id => document.getElementById(id).getBoundingClientRect());
    return {
      contained: controls.every(rect => rect.width > 0 && rect.left >= composer.left && rect.right <= composer.right && rect.bottom <= innerHeight),
      ordered: controls.every((rect, index) => index === 0 || controls[index - 1].right <= rect.left),
    };
  });
  await page.screenshot({ path: path.join(directory, 'steering-1000.png') });
  assert.deepEqual(layout, { contained: true, ordered: true });

  assert.equal(requests.length, 1);
  releaseFirst();
  await until(async () => requests.length === 4 && (await state()).status === 'idle', 'merged continuation and separate follow-ups');
  assert.equal(await prompt.inputValue(), '待编辑草稿');
  const requestText = (index, role) => requests[index].filter(message => !role || message.role === role).map(message => message.content).join('\n');
  assert.ok(requestText(1).includes(readResult));
  assert.equal(requestText(1, 'user').split(prompts.steer).length - 1, 1);
  assert.ok(!requestText(1).includes(prompts.followUp));
  assert.ok(!requestText(1).includes(prompts.shortcut));
  assert.ok(requestText(2, 'user').includes(prompts.followUp));
  assert.ok(!requestText(2).includes(prompts.shortcut));
  assert.ok(requestText(3, 'user').includes(prompts.shortcut));
  assert.equal((await state()).input_queue.length, 0);
  await delivery.waitFor({ state: 'hidden' });
  assert.deepEqual(await app.evaluate(() => process.steeringFixture), { submissions: 1, receiptQueries: 1 });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ directory, packaged, requests: requests.length, controlsFit: true, passed: true }));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (app) await app.close();
  server.closeAllConnections();
  server.close();
});
