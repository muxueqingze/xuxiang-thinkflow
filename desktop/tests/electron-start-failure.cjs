/* A real filesystem failure before run acceptance must not leave the UI running. */
const { _electron: electron } = require('playwright');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const directory = path.join(root, 'desktop/temp', `start-failure-${Date.now()}`);
const profile = path.join(directory, 'profile'), workspace = path.join(directory, 'workspace');
fs.mkdirSync(workspace, { recursive: true });
let app, snapshot, backup, obstacle, calls = 0;
const server = http.createServer((request, response) => {
  request.resume(); request.on('end', () => {
    calls++;
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.end('data: ' + JSON.stringify({ choices: [{ delta: { content: '本地故障恢复测试完成。' }, finish_reason: 'stop' }] }) + '\n\ndata: [DONE]\n\n');
  });
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const packagePath = JSON.parse(fs.readFileSync(path.join(root, 'artifacts/latest-desktop.json'), 'utf8')).path;
  app = await electron.launch({ executablePath: path.join(packagePath, 'ThinkFlow.exe'), args: [`--user-data-dir=${profile}`], env });
  const actual = await app.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath('userData') }));
  assert.equal(actual.packaged, true); assert.equal(path.resolve(actual.userData), profile);
  const page = await app.firstWindow();
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
  await app.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, workspace);
  await page.locator('#workspace').click();
  await page.locator('#settings-top').click();
  await page.locator('[name=model]').fill('本地故障测试');
  await page.locator('[name=base_url]').fill(`http://127.0.0.1:${server.address().port}`);
  await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  const initial = await page.evaluate(() => window.thinkflow.request('get_state'));
  const dataRoot = path.join(profile, 'data', 'workspaces');
  const relative = fs.readdirSync(dataRoot, { recursive: true }).find(file => file.endsWith(`${initial.session_id}.json`));
  assert.ok(relative); snapshot = path.join(dataRoot, relative); backup = `${snapshot}.test-backup`; obstacle = `${snapshot}.test-obstacle`;
  // Everything is inside this test profile. Keep the original bytes; no ACL edits or deletion.
  fs.renameSync(snapshot, backup); fs.mkdirSync(snapshot); fs.writeFileSync(path.join(snapshot, 'block'), 'fixture');
  await page.locator('#prompt').fill('存储恢复后只接收一次'); await page.locator('#send').click();
  await page.locator('.queue-confirm').waitFor({ state: 'visible' });
  const failed = await page.evaluate(() => window.thinkflow.request('get_state'));
  assert.equal(failed.status, 'idle'); assert.equal(failed.messages.length, 0); assert.equal(calls, 0);
  assert.equal(await page.locator('#stop').isVisible(), false);
  assert.equal(await page.locator('#new-session').isEnabled(), true);
  await page.screenshot({ path: path.join(directory, 'failure-remains-idle.png') });
  fs.renameSync(snapshot, obstacle); fs.renameSync(backup, snapshot); backup = null;
  await page.locator('.queue-confirm').click(); await page.locator('#resume-queue').click();
  await page.waitForFunction(async () => { const s = await window.thinkflow.request('get_state'); return s.status === 'idle' && s.messages.length === 2; });
  assert.equal(calls, 1);
  const result = { passed: true, packaged: true, failedStatus: failed.status, acceptedRequestsAfterRetry: calls, checks: 'real snapshot replace failure; no ghost run; controls available; uncertain input retained; explicit retry accepted once' };
  fs.writeFileSync(path.join(directory, 'results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ...result, evidence: directory }));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (backup && fs.existsSync(backup)) { if (fs.existsSync(snapshot)) fs.renameSync(snapshot, obstacle); fs.renameSync(backup, snapshot); }
  if (app) await app.close(); server.closeAllConnections(); server.close();
});
