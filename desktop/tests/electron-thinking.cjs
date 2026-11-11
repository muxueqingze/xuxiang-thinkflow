/* Manual Windows Electron check: isolated user data and a local SSE fixture. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const tempRoot = path.join(root, 'temp');
fs.mkdirSync(tempRoot, { recursive: true });
const directory = fs.mkdtempSync(path.join(tempRoot, 'desktop-thinking-'));
const workspace = path.join(directory, 'workspace');
const profile = path.join(directory, 'profile');
fs.mkdirSync(workspace);

const requests = [];
let catalogRequests = 0;
const server = http.createServer(async (request, response) => {
  if (request.method === 'GET') {
    catalogRequests++;
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{"data":[]}');
    return;
  }
  let raw = '';
  for await (const chunk of request) raw += chunk;
  const body = JSON.parse(raw);
  const options = Object.fromEntries(['thinking', 'reasoning_effort', 'max_tokens']
    .filter(key => Object.hasOwn(body, key)).map(key => [key, body[key]]));
  requests.push({ path: request.url, model: body.model, options });
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  if (request.url === '/v1/messages') {
    const events = [
      { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'fixture complete' } },
      { type: 'message_delta', delta: { stop_reason: 'end_turn' } },
      { type: 'message_stop' },
    ];
    response.end(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''));
  } else {
    const chunk = { choices: [{ index: 0, delta: { content: 'fixture complete' }, finish_reason: 'stop' }] };
    response.end(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`);
  }
});

let app, page;
const errors = [];
const state = () => page.evaluate(() => window.thinkflow.request('get_state', {}));
const control = id => page.locator(`#${id}`);
const field = name => page.locator(`#settings-form [name=${name}]`);

async function until(check, label) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await page.waitForTimeout(60);
  }
  throw new Error(`未等到 ${label}`);
}

async function verifyThinking(kind, setting, label) {
  await until(async () => {
    const thinking = (await state()).thinking;
    return thinking.kind === kind && thinking.setting === setting;
  }, `思考设置 ${kind} ${setting}`);
  await until(async () => (await control('thinking-label').textContent()) === label, `思考标签 ${label}`);
}

async function showThinkingChoices(expected) {
  await control('thinking-button').click();
  await control('thinking-popover').waitFor();
  const choices = await control('thinking-popover').locator('[data-thinking-choice]').evaluateAll(
    buttons => buttons.map(button => button.dataset.thinkingChoice),
  );
  assert.deepEqual(choices, expected.map(String));
}

async function selectThinking(value, kind, label) {
  await control('thinking-popover').locator(`[data-thinking-choice="${value}"]`).click();
  await control('thinking-popover').waitFor({ state: 'hidden' });
  await verifyThinking(kind, value, label);
}

async function turn(prompt, expected) {
  const previous = requests.length;
  await control('prompt').fill(prompt);
  await control('prompt').press('Enter');
  await until(() => requests.length === previous + 1, '本地 SSE 请求');
  await until(async () => {
    const current = await state();
    return current.status === 'idle' && current.messages.at(-1)?.content === 'fixture complete';
  }, 'SSE 任务完成');
  assert.deepEqual(requests[previous], expected);
}

async function saveSettings() {
  await control('save-settings').click();
  await control('settings-dialog').waitFor({ state: 'hidden' });
}

async function verify() {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const env = {
    ...process.env,
    THINKFLOW_TEST_USER_DATA: profile,
    PYTHONDONTWRITEBYTECODE: '1',
  };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ args: [path.join(root, 'desktop')], env });
  page = await app.firstWindow();
  page.on('pageerror', error => errors.push(error.message));
  await page.waitForFunction(() => document.getElementById('connection-label').textContent === '本地后端已连接');

  await control('settings-side').click();
  await field('model').fill('deepseek-flash');
  await field('base_url').fill(`http://127.0.0.1:${server.address().port}`);
  await field('api_key').fill('local-fixture');
  await control('settings-nav-advanced').click();
  await control('runtime-config').fill('{"context":{"enabled":false}}');
  await saveSettings();
  await app.evaluate(({ dialog }, cwd) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [cwd] });
  }, workspace);
  await control('workspace').click();
  await until(async () => Boolean((await state()).session_id), '新工作区会话');

  const deepseek = ['off', 'low', 'high', 'max'];
  await verifyThinking('deepseek', 'off', '关闭');
  await showThinkingChoices(deepseek);
  assert.equal(await page.locator('[data-thinking-choice=off]').getAttribute('aria-pressed'), 'true');
  await selectThinking('low', 'deepseek', '低');
  assert.equal(requests.length, 0);
  await turn('DeepSeek low', { path: '/v1/chat/completions', model: 'deepseek-flash',
    options: { thinking: { type: 'enabled' }, reasoning_effort: 'low' } });
  await showThinkingChoices(deepseek);
  await selectThinking('off', 'deepseek', '关闭');
  assert.equal(requests.length, 1);
  await turn('DeepSeek off', { path: '/v1/chat/completions', model: 'deepseek-flash',
    options: { thinking: { type: 'disabled' } } });

  await control('settings-top').click();
  await control('model-picker-search').fill('fixture-compatible');
  await control('model-picker-search').press('Enter');
  await verifyThinking('openai', 'default', '默认');
  const compatible = ['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
  await showThinkingChoices(compatible);
  await selectThinking('default', 'openai', '默认');
  assert.equal(requests.length, 2);
  await turn('OpenAI model default', { path: '/v1/chat/completions', model: 'fixture-compatible', options: {} });
  await showThinkingChoices(compatible);
  await selectThinking('xhigh', 'openai', '极高');
  assert.equal(requests.length, 3);
  await turn('OpenAI xhigh', { path: '/v1/chat/completions', model: 'fixture-compatible',
    options: { reasoning_effort: 'xhigh' } });

  await control('settings-side').click();
  await field('provider').selectOption('anthropic');
  await field('model').fill('fixture-claude');
  await control('settings-nav-context').click();
  await field('max_tokens').fill('8192');
  await saveSettings();
  await verifyThinking('anthropic', 0, '关闭');
  await showThinkingChoices([0, 1024, 4096]);
  await selectThinking(4096, 'anthropic', '4.1k');
  assert.equal(await control('thinking-button').getAttribute('title'), '思考 · 4,096 tokens');
  assert.equal(requests.length, 4);
  await turn('Anthropic preset', { path: '/v1/messages', model: 'fixture-claude',
    options: { max_tokens: 8192, thinking: { type: 'enabled', budget_tokens: 4096 } } });

  await showThinkingChoices([0, 1024, 4096]);
  await control('thinking-popover').getByRole('button', { name: '自定义预算…' }).click();
  await field('thinking_budget').fill('8192');
  await control('save-settings').click();
  await control('settings-error').waitFor();
  assert.match(await control('settings-error').textContent(), /思考预算.*小于输出 token 上限/);
  assert.equal(await field('thinking_budget').inputValue(), '8192');
  assert.equal((await state()).config.thinking_budget, 4096);
  assert.equal(requests.length, 5);
  await control('cancel-settings').click();
  await showThinkingChoices([0, 1024, 4096]);
  await control('thinking-popover').getByRole('button', { name: '自定义预算…' }).click();
  assert.equal(await field('thinking_budget').inputValue(), '4096');
  await field('thinking_budget').fill('3072');
  await saveSettings();
  await verifyThinking('anthropic', 3072, '3.1k');
  assert.equal(requests.length, 5);
  await turn('Anthropic custom', { path: '/v1/messages', model: 'fixture-claude',
    options: { max_tokens: 8192, thinking: { type: 'enabled', budget_tokens: 3072 } } });

  assert.equal(requests.length, 6);
  assert.equal(catalogRequests, 0);
  assert.deepEqual(errors, []);
  return { passed: true, modelRequests: requests.length, catalogRequests, requests,
    invalidBudget: { attempted: 8192, preserved: 4096 }, pageErrors: errors };
}

function recycleFixture() {
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `
    $fixtureRoot = (Resolve-Path -LiteralPath $env:THINKFLOW_THINKING_FIXTURE_ROOT).Path
    $fixturePath = (Resolve-Path -LiteralPath $env:THINKFLOW_THINKING_FIXTURE_DIR).Path
    if ([IO.Path]::GetDirectoryName($fixturePath) -ne $fixtureRoot -or
        [IO.Path]::GetFileName($fixturePath) -notlike 'desktop-thinking-*') {
      throw 'Fixture path is outside the expected temporary directory'
    }
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($fixturePath, 'OnlyErrorDialogs', 'SendToRecycleBin')
  `], {
    env: { ...process.env, THINKFLOW_THINKING_FIXTURE_ROOT: tempRoot, THINKFLOW_THINKING_FIXTURE_DIR: directory },
    windowsHide: true,
  });
  assert.equal(fs.existsSync(directory), false);
}

async function main() {
  let result;
  try {
    result = await verify();
  } finally {
    try {
      if (app) await app.close();
    } finally {
      if (server.listening) {
        await new Promise(resolve => {
          server.close(resolve);
          server.closeAllConnections();
        });
      }
      recycleFixture();
    }
  }
  console.log(JSON.stringify(result, null, 2));
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
