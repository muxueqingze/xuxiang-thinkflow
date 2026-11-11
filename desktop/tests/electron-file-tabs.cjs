/* Manual Windows Electron check with isolated user data and workspaces. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const tempRoot = path.join(root, 'temp');
fs.mkdirSync(tempRoot, { recursive: true });
const directory = fs.mkdtempSync(path.join(tempRoot, 'desktop-file-tabs-'));
const workspace = path.join(directory, 'workspace');
const nested = path.join(workspace, 'nested');
const otherWorkspace = path.join(directory, 'other-workspace');
const profile = path.join(directory, 'profile');
let app, page;

function prepareWorkspaces() {
  fs.mkdirSync(nested, { recursive: true });
  execFileSync('git', ['init', '-q'], { cwd: workspace, windowsHide: true });
  for (const name of ['alpha', 'beta']) {
    const text = Array.from({ length: 200 }, (_, index) => `${name} ${index + 1} ${'text '.repeat(70)}`).join('\n');
    fs.writeFileSync(path.join(nested, `${name}.txt`), `${text}\n`);
  }
  fs.mkdirSync(otherWorkspace);
  fs.writeFileSync(path.join(otherWorkspace, 'other.txt'), 'different workspace\n');
}

const control = id => page.locator(`#${id}`);
const file = name => control('files-list').locator(`[data-path="${name}"]`);
const tab = name => control('file-open-tabs').locator(`[data-file-tab-path="nested/${name}.txt"]`);
const getState = () => page.evaluate(() => window.thinkflow.request('get_state', {}));

async function openWorkspace(cwd) {
  await app.evaluate(({ dialog }, target) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [target] });
  }, cwd);
  await control('workspace').click();
}

async function expectPreview(name, text) {
  await page.waitForFunction(({ name, text }) => {
    const title = document.getElementById('preview-file-name');
    return title.textContent === `nested/${name}.txt` && document.getElementById('preview-file-content').textContent.includes(text);
  }, { name, text });
  assert.equal(await tab(name).getAttribute('data-active'), 'true');
  assert.match(await control('preview-file-info').textContent(), /只读 · 版本 [a-f0-9]{12}/);
}

async function scrollPreview(top, left) {
  return control('preview-file-content').evaluate((element, { top, left }) => {
    element.scrollTop = top;
    element.scrollLeft = left;
    return [element.scrollTop, element.scrollLeft];
  }, { top, left });
}

async function expectScroll(expected) {
  const offset = await control('preview-file-content').evaluate(element => [element.scrollTop, element.scrollLeft]);
  assert.deepEqual(offset, expected);
}

async function select(name) {
  await tab(name).locator('.file-tab-name').click();
}

async function verify() {
  prepareWorkspaces();
  const env = { ...process.env, THINKFLOW_TEST_USER_DATA: profile, PYTHONDONTWRITEBYTECODE: '1' };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ args: [path.join(root, 'desktop')], env });
  page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');
  await openWorkspace(workspace);
  await control('open-files').click();
  await file('nested').click();
  await file('nested/alpha.txt').click();
  await expectPreview('alpha', 'alpha 200');
  const alphaScroll = await scrollPreview(300, 90);
  assert.ok(alphaScroll.every(value => value > 0));
  await control('close-file-preview').click();
  assert.equal(await control('files-path').textContent(), 'nested');
  await file('nested/beta.txt').click();
  await expectPreview('beta', 'beta 200');
  const oldBetaVersion = await control('preview-file-info').textContent();
  const betaScroll = await scrollPreview(130, 45);
  assert.ok(betaScroll.every(value => value > 0));
  await select('alpha');
  await expectPreview('alpha', 'alpha 200');
  await expectScroll(alphaScroll);
  const oldAlphaVersion = await control('preview-file-info').textContent();

  fs.appendFileSync(path.join(nested, 'alpha.txt'), 'alpha refreshed\n');
  fs.appendFileSync(path.join(nested, 'beta.txt'), 'beta changed on disk\n');
  await page.evaluate(() => {
    document.getElementById('refresh-files').click();
    document.querySelector('[data-file-tab-path="nested/beta.txt"] .file-tab-name').click();
  });
  await page.waitForFunction(() => fileTabs.get('nested/alpha.txt').snapshot.content.includes('alpha refreshed'));
  await expectPreview('beta', 'beta 200');
  assert.equal(await control('preview-file-info').textContent(), oldBetaVersion);
  assert.equal((await control('preview-file-content').textContent()).includes('beta changed on disk'), false);
  await expectScroll(betaScroll);
  await select('alpha');
  await expectPreview('alpha', 'alpha refreshed');
  assert.notEqual(await control('preview-file-info').textContent(), oldAlphaVersion);
  await expectScroll(alphaScroll);

  await control('close-ledger').click();
  await page.waitForFunction(() => document.getElementById('inspector').inert);
  await control('open-files').click();
  await expectPreview('alpha', 'alpha refreshed');
  await expectScroll(alphaScroll);

  await control('open-changes').click();
  const gitRow = page.locator('.git-file-details').filter({ has: page.locator('summary[data-git-path="nested/alpha.txt"]') });
  await gitRow.locator('summary').click();
  await gitRow.locator('.diff-text').filter({ hasText: '+alpha refreshed' }).waitFor();
  await control('open-files').click();
  const session = (await getState()).session_id;
  await control('new-session').click();
  await page.waitForFunction(async old => (await window.thinkflow.request('get_state', {})).session_id !== old, session);
  await expectPreview('alpha', 'alpha refreshed');
  await expectScroll(alphaScroll);
  assert.equal(await control('file-open-tabs').locator('.file-tab').count(), 2);
  await control('open-changes').click();
  assert.equal(await gitRow.evaluate(row => row.open), true);
  await control('open-files').click();

  await control('attach-file').click();
  await file('nested/beta.txt').waitFor();
  assert.equal(await control('close-file-preview').getAttribute('aria-pressed'), 'true');
  assert.equal(await control('file-open-tabs').locator('.file-tab').count(), 2);
  await file('nested/beta.txt').click();
  await expectPreview('beta', 'beta 200');
  await control('refresh-files').click();
  await expectPreview('beta', 'beta changed on disk');
  await expectScroll(betaScroll);
  await tab('beta').locator('.file-tab-close').click();
  await expectPreview('alpha', 'alpha refreshed');
  assert.equal(await control('open-files').getAttribute('aria-pressed'), 'true');
  assert.equal(await control('file-open-tabs').locator('.file-tab').count(), 1);

  await openWorkspace(otherWorkspace);
  await file('other.txt').waitFor();
  assert.equal(await control('files-path').textContent(), '工作区');
  assert.equal(await control('file-open-tabs').locator('.file-tab').count(), 0);
  assert.equal(await control('file-preview').isVisible(), false);
  assert.equal(await control('git-file-list').locator('.git-file-details').count(), 0);
  assert.deepEqual(errors, []);
}

function recycleFixture() {
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `
    $fixtureRoot = (Resolve-Path -LiteralPath $env:THINKFLOW_FILE_TABS_ROOT).Path
    $fixturePath = (Resolve-Path -LiteralPath $env:THINKFLOW_FILE_TABS_DIR).Path
    if ([IO.Path]::GetDirectoryName($fixturePath) -ne $fixtureRoot -or
        [IO.Path]::GetFileName($fixturePath) -notlike 'desktop-file-tabs-*') {
      throw 'Fixture path is outside the expected temporary directory'
    }
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($fixturePath, 'OnlyErrorDialogs', 'SendToRecycleBin')
  `], {
    env: { ...process.env, THINKFLOW_FILE_TABS_ROOT: tempRoot, THINKFLOW_FILE_TABS_DIR: directory },
    windowsHide: true,
  });
  assert.equal(fs.existsSync(directory), false);
}

async function main() {
  try {
    await verify();
  } finally {
    try {
      if (app) await app.close();
    } finally {
      recycleFixture();
    }
  }
  console.log('File tabs Electron fixture passed: snapshots, scroll, refresh, attachment list and workspace lifecycle.');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
