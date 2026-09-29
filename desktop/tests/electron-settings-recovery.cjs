/* Targeted regression: damaged encrypted settings remain repairable in the UI. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { DEFAULTS } = require('../settings.cjs');
const directory = path.resolve(__dirname, '../temp', `settings-recovery-${Date.now()}`);
const userData = path.join(directory, 'user-data');
fs.mkdirSync(userData, { recursive: true });
fs.writeFileSync(path.join(userData, 'settings.json'), JSON.stringify({ config: { ...DEFAULTS, base_url: 'http://127.0.0.1:9', model: 'fake-local-model' }, encrypted_key: Buffer.from('damaged-ciphertext').toString('base64') }));
let application;
async function launch() {
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: userData }; delete env.ELECTRON_RUN_AS_NODE;
  application = await electron.launch({ args: [path.resolve(__dirname, '..')], env });
  const page = await application.firstWindow();
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接', null, { timeout: 20000 });
  return page;
}
async function main() {
  let page = await launch();
  let state = await page.evaluate(() => window.thinkflow.request('get_state'));
  assert.match(state.settings_warning, /无法解锁/); assert.equal(state.config.has_api_key, false);
  assert.equal(await page.locator('#settings-top').isDisabled(), false);
  await page.locator('#settings-top').click();
  await page.locator('[name=api_key]').fill('fake-replacement-secret');
  await page.locator('#save-settings').click();
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);
  state = await page.evaluate(() => window.thinkflow.request('get_state'));
  assert.equal(state.config.has_api_key, true); assert.equal(state.settings_warning, undefined);
  assert.equal(fs.readFileSync(path.join(userData, 'settings.json'), 'utf8').includes('fake-replacement-secret'), false);
  await application.close(); application = null;
  page = await launch();
  state = await page.evaluate(() => window.thinkflow.request('get_state'));
  assert.equal(state.config.has_api_key, true); assert.equal(state.settings_warning, undefined);
  console.log(JSON.stringify({ passed: true, checks: 'damaged Windows ciphertext: UI starts, configuration repairs key, encrypted replacement survives restart', evidence: directory }, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (application) await application.close(); });
