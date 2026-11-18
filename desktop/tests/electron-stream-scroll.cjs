/* Real Electron renderer, IPC and loopback SSE. No user profile or model key. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { _electron: electron } = require('playwright');

const root = path.resolve(__dirname, '../..');
const testRoot = path.join(root, 'desktop/temp');
fs.mkdirSync(testRoot, { recursive: true });
const fixture = fs.mkdtempSync(path.join(testRoot, 'stream-scroll-'));
const profile = path.join(fixture, 'profile'), workspace = path.join(fixture, 'workspace');
fs.mkdirSync(workspace);
const launcher = path.join(fixture, 'launch.cjs');
fs.writeFileSync(launcher, `
  const { app, BrowserWindow } = require('electron');
  BrowserWindow.prototype.show = function () {};
  app.on('browser-window-created', (_, win) => win.webContents.setBackgroundThrottling(false));
  require(${JSON.stringify(path.join(root, 'desktop/main.cjs'))});
`);

let connection, app;
const endpoint = http.createServer((request, response) => {
  request.resume();
  request.on('end', () => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    connection = response;
    send('# 流式验收\n\n');
  });
});
function send(text) {
  connection.write('data: ' + JSON.stringify({ choices: [{ delta: { content: text } }] }) + '\n\n');
}

async function main() {
  await new Promise(resolve => endpoint.listen(0, '127.0.0.1', resolve));
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: profile, THINKFLOW_HOME: path.join(fixture, 'home') };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.argv[2];
  app = await electron.launch(executablePath ? { executablePath, args: [`--user-data-dir=${profile}`], env } : { args: [launcher], env });
  assert.equal(path.resolve(await app.evaluate(({ app }) => app.getPath('userData'))), profile);
  const page = await app.firstWindow(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  if (executablePath) await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) { win.hide(); win.webContents.setBackgroundThrottling(false); }
  });
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
  await app.evaluate(({ dialog }, cwd) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [cwd] }); }, workspace);
  await page.locator('#workspace').click();
  await page.locator('#settings-side').click();
  await page.locator('[name=model]').fill('scroll-fixture');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${endpoint.address().port}`);
  await page.locator('#save-settings').click();
  await page.locator('#settings-dialog').waitFor({ state: 'hidden' });
  await page.locator('#prompt').fill('输出足够滚屏的 Markdown。');
  await page.locator('#prompt').press('Enter');
  const heading = page.locator('.message-body .markdown-heading');
  await heading.waitFor();
  const title = await heading.elementHandle();
  const initial = Array.from({ length: 75 }, (_, n) => `段落 ${n} 包含 **粗体** 和 \`行内代码\`。`).join('\n\n');
  send(initial + '\n\n```js\nconst first = 1;\n');
  const code = page.locator('.code-block pre code');
  await code.waitFor();
  await page.waitForFunction(() => document.querySelector('.code-block pre code')?.textContent.trimEnd().endsWith('1;'));
  const codeText = await code.evaluateHandle(node => node.firstChild);
  assert.equal(await title.evaluate(node => node.isConnected), true, 'heading removed by token update');
  const box = page.locator('#messages-scroll');
  const gap = () => box.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop);
  assert.ok(Math.abs(await gap()) <= 1, 'follow bottom');
  // One wheel notch (or even less) must let the reader stay above the tail.
  const top = await box.evaluate(el => { el.scrollTop -= 32; return el.scrollTop; });
  await page.locator('#back-to-bottom').waitFor();
  send('const second = "<script>";\n');
  await page.waitForFunction(() => document.querySelector('.code-block pre code').textContent.includes('second'));
  assert.equal(await codeText.evaluate(node => node.isConnected), true, 'stream replaced active code text');
  assert.equal(await box.evaluate(el => el.scrollTop), top, 'new tokens moved the reader');
  const copied = await code.textContent();
  assert.equal(copied.trimEnd(), 'const first = 1;\nconst second = "<script>";');
  assert.equal(await code.locator('script').count(), 0);

  await page.locator('#back-to-bottom').click();
  send('```\n\n3. 三\n4. 四\n\n最终段落');
  await page.waitForFunction(() => document.querySelector('.message.assistant .message-body').textContent.includes('最终段落'));
  const before = await page.locator('.message.assistant .message-body').innerHTML();
  connection.end('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
  await page.waitForFunction(() => !running());
  assert.equal(await page.locator('.message.assistant .message-body').innerHTML(), before);
  assert.equal(await codeText.evaluate(node => node.isConnected), true);
  assert.equal(await page.locator('.markdown-list li').count(), 2);
  assert.ok(Math.abs(await gap()) <= 1, 'finished reply follows bottom');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ paragraphs: 75, readerScrollTop: top, finalGap: await gap(), keptHeadingAndCode: true, fixture }));
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  endpoint.closeAllConnections();
  if (app) await app.close();
  endpoint.close();
  fs.rmSync(fixture, { recursive: true });
});
