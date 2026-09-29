const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { NavigationStore, openWorkspace, openRecentWorkspace, restoreWorkspace } = require('../navigation.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'thinkflow-navigation-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspace = path.join(root, 'workspace');
  const other = path.join(root, 'other');
  fs.mkdirSync(workspace); fs.mkdirSync(other);
  const store = new NavigationStore(path.join(root, 'user-data'));
  return { root, workspace, other, store };
}
const first = 'a'.repeat(32), second = 'b'.repeat(32);

test('navigation persists recent ordering, selected sessions and caps history at 20', t => {
  const { root, workspace, other, store } = fixture(t);
  store.remember({ cwd: workspace, session_id: first });
  store.remember({ cwd: other, session_id: second });
  store.remember({ cwd: workspace, session_id: second });
  const restarted = new NavigationStore(path.dirname(store.file)); restarted.load();
  assert.deepEqual(restarted.recent(), [{ path: workspace, name: 'workspace' }, { path: other, name: 'other' }]);
  assert.equal(restarted.registered(workspace).last_session_id, second);
  assert.equal(restarted.current.last_workspace, workspace);
  for (let index = 0; index < 22; index++) restarted.remember({ cwd: path.join(root, String(index)), session_id: first });
  assert.equal(restarted.recent().length, 20);
  assert.equal(restarted.registered(workspace), null);
  assert.equal(JSON.parse(fs.readFileSync(store.file, 'utf8')).workspaces.length, 20);
});

test('recent workspace accepts only registered absolute paths and never forwards arbitrary renderer paths', async t => {
  const { root, workspace, store } = fixture(t);
  store.remember({ cwd: workspace, session_id: first });
  const requests = [];
  const backend = { request: async (method, params) => { requests.push({ method, params }); return { cwd: params.cwd, session_id: second }; } };
  for (const directory of [undefined, null, {}, '', '../workspace', path.join(root, 'unregistered'), `${workspace}-suffix`]) await assert.rejects(openRecentWorkspace(backend, store, directory), /最近工作区/);
  assert.equal(requests.length, 0);
  const result = await openRecentWorkspace(backend, store, workspace);
  assert.equal(result.session_id, second);
  assert.deepEqual(requests, [{ method: 'open_workspace', params: { cwd: workspace, session_id: first } }]);
  const neverChosen = path.join(root, 'dialog-selected');
  await openWorkspace(backend, store, neverChosen);
  assert.deepEqual(requests[1], { method: 'open_workspace', params: { cwd: neverChosen } });
});

test('restart restores last workspace and session without a run request', async t => {
  const { workspace, other, store } = fixture(t);
  store.remember({ cwd: workspace, session_id: first });
  store.remember({ cwd: other, session_id: second });
  const restarted = new NavigationStore(path.dirname(store.file)); restarted.load();
  const requests = [];
  const backend = { request: async (method, params) => { requests.push({ method, params }); return { cwd: params.cwd, session_id: params.session_id, recovery_required: true }; } };
  const result = await restoreWorkspace(backend, restarted);
  assert.equal(result.state.cwd, other);
  assert.equal(result.state.session_id, second);
  assert.equal(result.state.recovery_required, true);
  assert.deepEqual(requests.map(item => item.method), ['open_workspace']);
});

test('missing or unavailable directories are skipped with warning without rewriting history', async t => {
  const { root, workspace, store } = fixture(t);
  store.remember({ cwd: workspace, session_id: first });
  store.remember({ cwd: path.join(root, 'missing'), session_id: second });
  const before = fs.readFileSync(store.file, 'utf8');
  const calls = [];
  const backend = { request: async (method, params) => { calls.push(params.cwd); return { cwd: params.cwd, session_id: first }; } };
  const result = await restoreWorkspace(backend, store);
  assert.equal(result.state.cwd, workspace);
  assert.match(result.warning, /已跳过/);
  assert.deepEqual(calls, [workspace]);
  assert.equal(fs.readFileSync(store.file, 'utf8'), before);
  const unavailable = await restoreWorkspace({ request: async () => { throw new Error('local failure'); } }, store);
  assert.equal(unavailable.state, null);
  assert.match(unavailable.warning, /无法恢复/);
});

test('corrupt navigation stays untouched until a successful user selection, then is backed up before repair', t => {
  const { workspace, store } = fixture(t);
  fs.mkdirSync(path.dirname(store.file), { recursive: true });
  fs.writeFileSync(store.file, '{"incomplete":', 'utf8');
  assert.throws(() => store.load(), /原文件已保留/);
  assert.deepEqual(store.recent(), []);
  assert.equal(fs.readFileSync(store.file, 'utf8'), '{"incomplete":');
  store.remember({ cwd: workspace, session_id: first });
  assert.equal(store.registered(workspace).last_session_id, first);
  assert.equal(fs.readFileSync(store.recoveryBackup, 'utf8'), '{"incomplete":');
  const restarted = new NavigationStore(path.dirname(store.file)); restarted.load();
  assert.equal(restarted.registered(workspace).last_session_id, first);
});

test('invalid navigation schema is rejected and settings file is untouched', t => {
  const { workspace, store } = fixture(t);
  fs.mkdirSync(path.dirname(store.file), { recursive: true });
  const settings = path.join(path.dirname(store.file), 'settings.json');
  fs.writeFileSync(settings, '{"encrypted_key":"preserve-existing-key"}', 'utf8');
  for (const invalid of [{}, { version: 1, last_workspace: workspace, workspaces: [] }, { version: 1, last_workspace: workspace, workspaces: [{ path: workspace, last_session_id: '../escape' }] }]) {
    fs.writeFileSync(store.file, JSON.stringify(invalid), 'utf8');
    const restarted = new NavigationStore(path.dirname(store.file));
    assert.throws(() => restarted.load(), /无法读取/);
    assert.equal(fs.readFileSync(store.file, 'utf8'), JSON.stringify(invalid));
  }
  assert.equal(fs.readFileSync(settings, 'utf8'), '{"encrypted_key":"preserve-existing-key"}');
});
