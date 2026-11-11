/* Manual Windows Electron check with isolated user data and Git workspaces. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const tempRoot = path.join(root, 'temp');
fs.mkdirSync(tempRoot, { recursive: true });
const directory = fs.mkdtempSync(path.join(tempRoot, 'desktop-git-inline-'));
const workspace = path.join(directory, 'workspace');
const otherWorkspace = path.join(directory, 'other-workspace');
const profile = path.join(directory, 'profile');
let app, page;

function git(cwd, ...args) {
  execFileSync('git', args, { cwd, stdio: 'pipe', windowsHide: true });
}

function prepareWorkspaces() {
  fs.mkdirSync(workspace);
  git(workspace, 'init', '-q');
  fs.writeFileSync(path.join(workspace, 'tracked.txt'), 'tracked original\n');
  fs.writeFileSync(path.join(workspace, 'second.txt'), 'second original\n');
  git(workspace, 'add', 'tracked.txt', 'second.txt');
  git(workspace, '-c', 'user.name=Local fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture');
  fs.writeFileSync(path.join(workspace, 'tracked.txt'), 'staged line\n');
  git(workspace, 'add', 'tracked.txt');
  fs.appendFileSync(path.join(workspace, 'tracked.txt'), 'unstaged line\n');
  fs.appendFileSync(path.join(workspace, 'second.txt'), 'second changed\n');
  fs.writeFileSync(path.join(workspace, 'untracked.txt'), 'untracked line\n');

  fs.mkdirSync(otherWorkspace);
  git(otherWorkspace, 'init', '-q');
  fs.writeFileSync(path.join(otherWorkspace, 'tracked.txt'), 'other workspace line\n');
}

function row(group, file) {
  return page.locator(`.git-file-details[data-git-group="${group}"]`).filter({
    has: page.locator(`summary[data-git-path="${file}"]`),
  });
}

async function line(entry, text) {
  const content = entry.locator('.git-inline-diff .diff-text').filter({ hasText: text });
  await content.waitFor();
  assert.equal(await content.textContent(), text);
  return content.locator('xpath=..');
}

async function openWorkspace(cwd) {
  await app.evaluate(({ dialog }, target) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [target] });
  }, cwd);
  await page.locator('#workspace').click();
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
  await page.locator('#open-changes').click();
  const staged = row('staged', 'tracked.txt'), unstaged = row('unstaged', 'tracked.txt');
  const second = row('unstaged', 'second.txt'), untracked = row('untracked', 'untracked.txt');
  await untracked.waitFor();
  assert.deepEqual(await page.locator('#git-file-list .git-group-title').allTextContents(), [
    '已暂存 · 1', '未暂存 · 2', '未跟踪 · 1',
  ]);

  await page.evaluate(() => {
    for (const summary of document.querySelectorAll('summary[data-git-path="tracked.txt"], summary[data-git-path="second.txt"]')) {
      summary.click();
    }
  });
  const oldLine = await line(staged, '-tracked original');
  const newLine = await line(staged, '+staged line');
  assert.equal(await oldLine.getAttribute('data-old-line'), '1');
  assert.equal(await oldLine.getAttribute('data-new-line'), null);
  assert.equal(await newLine.getAttribute('data-old-line'), null);
  assert.equal(await newLine.getAttribute('data-new-line'), '1');
  assert.equal(await newLine.textContent(), '+staged line');
  assert.equal(await (await line(unstaged, '+unstaged line')).getAttribute('data-new-line'), '2');
  await line(second, '+second changed');
  assert.equal(await page.locator('#git-file-list .git-file-details[open]').count(), 3);
  assert.equal(await page.locator('#git-file-list').isVisible(), true);

  await staged.evaluate(node => { node.dataset.fixtureIdentity = 'staged'; });
  fs.appendFileSync(path.join(workspace, 'second.txt'), 'new after refresh\n');
  await page.locator('#refresh-git').click();
  await line(second, '+new after refresh');
  assert.equal(await staged.getAttribute('data-fixture-identity'), 'staged');
  assert.equal(await page.locator('#git-file-list .git-file-details[open]').count(), 3);

  await page.locator('#close-ledger').click();
  await page.waitForFunction(() => {
    const inspector = document.getElementById('inspector');
    return inspector.inert && inspector.getAttribute('aria-hidden') === 'true';
  });
  assert.equal(await staged.getAttribute('data-fixture-identity'), 'staged');
  await page.locator('#open-changes').click();
  await line(second, '+new after refresh');
  assert.equal(await staged.getAttribute('data-fixture-identity'), 'staged');
  assert.equal(await page.locator('#git-file-list .git-file-details[open]').count(), 3);

  await untracked.locator('summary').click();
  assert.equal(await (await line(untracked, '+untracked line')).getAttribute('data-new-line'), '1');
  assert.equal(await page.locator('#git-file-list .git-file-details[open]').count(), 4);

  await openWorkspace(otherWorkspace);
  const other = row('untracked', 'tracked.txt');
  await other.waitFor();
  assert.equal(await page.locator('#git-file-list .git-file-details').count(), 1);
  assert.equal(await page.locator('#git-file-list .git-file-details[open]').count(), 0);
  await other.locator('summary').click();
  await line(other, '+other workspace line');

  const sharedRender = await page.evaluate(() => {
    const preview = document.createElement('pre');
    renderDiff(preview, '@@ -1 +1 @@\n-old\n+new');
    return [...preview.children].map(node => [node.className, node.textContent, node.childElementCount]);
  });
  assert.deepEqual(sharedRender, [
    ['diff-line diff-hunk', '@@ -1 +1 @@', 0],
    ['diff-line diff-remove', '-old', 0],
    ['diff-line diff-add', '+new', 0],
  ]);
  assert.deepEqual(errors, []);
}

function recycleFixture() {
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `
    $fixtureRoot = (Resolve-Path -LiteralPath $env:THINKFLOW_GIT_INLINE_ROOT).Path
    $fixturePath = (Resolve-Path -LiteralPath $env:THINKFLOW_GIT_INLINE_DIR).Path
    if ([IO.Path]::GetDirectoryName($fixturePath) -ne $fixtureRoot -or
        [IO.Path]::GetFileName($fixturePath) -notlike 'desktop-git-inline-*') {
      throw 'Fixture path is outside the expected temporary directory'
    }
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($fixturePath, 'OnlyErrorDialogs', 'SendToRecycleBin')
  `], {
    env: { ...process.env, THINKFLOW_GIT_INLINE_ROOT: tempRoot, THINKFLOW_GIT_INLINE_DIR: directory },
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
  console.log('Git inline Electron fixture passed: staging, concurrent expansion, refresh, pane and workspace switching.');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
