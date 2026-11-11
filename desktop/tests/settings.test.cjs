const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { SettingsStore, DEFAULTS, RUNTIME_CONFIG_FIELDS, normalizeConfig } = require('../settings.cjs');
const encrypted = { isEncryptionAvailable: () => true, encryptString: text => Buffer.from(text.split('').reverse().join('')), decryptString: bytes => bytes.toString().split('').reverse().join('') };
function temporarySettings(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'thinkflow-settings-'));
  t.after(() => {
    const file = path.join(directory, 'settings.json');
    if (fs.existsSync(file)) fs.unlinkSync(file);
    fs.rmdirSync(directory);
  });
  return directory;
}
function ipcValidation() {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  return vm.runInNewContext(source.slice(source.indexOf('function validateParams('), source.indexOf('function cleanError(')) + '\nvalidateParams', { RUNTIME_CONFIG_FIELDS });
}
test('only ciphertext persists; blank preserves and explicit clear removes a key', t => {
  const directory = temporarySettings(t);
  const store = new SettingsStore(directory, encrypted);
  const secret = 'fake-test-secret-123';
  const initial = store.prepare({ ...DEFAULTS, api_key: secret });
  store.save(initial);
  assert.equal(fs.readFileSync(store.file, 'utf8').includes(secret), false);
  assert.equal(store.prepare({ ...DEFAULTS, api_key: '' }).backend.api_key, secret);
  const loaded = new SettingsStore(directory, encrypted); loaded.load();
  assert.equal(loaded.backendConfig().api_key, secret);
  assert.equal(loaded.backendConfig().max_tokens, null);
  const cleared = loaded.prepare({ ...DEFAULTS, clear_api_key: true }); loaded.save(cleared);
  assert.equal(loaded.backendConfig().api_key, '');
  assert.equal(JSON.parse(fs.readFileSync(loaded.file, 'utf8')).encrypted_key, '');
});
test('optional output budget follows provider protocol without injecting a default', () => {
  for (const max_tokens of [null, undefined, '', 0]) assert.equal(normalizeConfig({ ...DEFAULTS, max_tokens }).max_tokens, null);
  assert.equal(normalizeConfig({ ...DEFAULTS, max_tokens: 8192 }).max_tokens, 8192);
  assert.throws(() => normalizeConfig({ ...DEFAULTS, provider: 'anthropic' }), /Anthropic/);
  assert.equal(normalizeConfig({ ...DEFAULTS, provider: 'anthropic', max_tokens: 8192 }).max_tokens, 8192);
});

test('tool and engineering guidance choices survive configure, save and restart', t => {
  const validateParams = ipcValidation();
  const files = new Map();
  t.mock.method(fs, 'existsSync', file => files.has(file));
  t.mock.method(fs, 'mkdirSync', () => {});
  t.mock.method(fs, 'writeFileSync', (file, data) => files.set(file, data));
  t.mock.method(fs, 'renameSync', (from, to) => { files.set(to, files.get(from)); files.delete(from); });
  t.mock.method(fs, 'readFileSync', file => files.get(file));
  assert.equal(DEFAULTS.enable_native_tools, false);
  const legacy = { ...DEFAULTS }; delete legacy.enable_native_tools;
  assert.equal(normalizeConfig(legacy).enable_native_tools, false);
  delete legacy.use_builtin_system_prompt; delete legacy.system_prompt;
  assert.equal(normalizeConfig(legacy).use_builtin_system_prompt, true);
  assert.equal(normalizeConfig(legacy).system_prompt, '');
  const store = new SettingsStore('in-memory-settings', encrypted);
  for (const enabled of [true, false]) {
    const input = { ...DEFAULTS, enable_native_tools: enabled };
    assert.doesNotThrow(() => validateParams('configure', input));
    const prepared = store.prepare(input);
    assert.equal(prepared.backend.enable_native_tools, enabled);
    store.save(prepared);
    assert.equal(JSON.parse(files.get(store.file)).config.enable_native_tools, enabled);
    const restarted = new SettingsStore('in-memory-settings', encrypted); restarted.load();
    assert.equal(restarted.backendConfig().enable_native_tools, enabled);
  }
  assert.throws(() => normalizeConfig({ ...DEFAULTS, enable_native_tools: 'false' }), /布尔值/);
  for (const guidance of [
    { use_builtin_system_prompt: true, system_prompt: '' },
    { use_builtin_system_prompt: false, system_prompt: '  自定义指引\r\n保留原文与空白  ' },
    { use_builtin_system_prompt: false, system_prompt: '' },
  ]) {
    const input = { ...DEFAULTS, ...guidance };
    assert.doesNotThrow(() => validateParams('configure', input));
    const prepared = store.prepare(input); store.save(prepared);
    const restarted = new SettingsStore('in-memory-settings', encrypted); restarted.load();
    for (const key of ['use_builtin_system_prompt', 'system_prompt']) {
      assert.equal(prepared.backend[key], guidance[key]);
      assert.equal(restarted.backendConfig()[key], guidance[key]);
    }
  }
});
test('runtime overrides reach configure, survive restart and can be removed from the editor', t => {
  const directory = temporarySettings(t);
  const validateParams = ipcValidation();
  const store = new SettingsStore(directory, encrypted);
  const overrides = {
    compaction: { enabled: false }, context: { files: ['guide.md'] }, interfaces: { web: { enabled: false } },
    tool_protocol: { allow_legacy_tags: true }, max_read_chars: 4096, max_retries: 0, retry_backoff_seconds: 0.5,
    max_auto_continues: 1, max_consecutive_failures: 4, delivery_verify: true, auto_verify_runnable_artifacts: false,
    max_delivery_fix_attempts: 2, native_tools: ['read'], disabled_native_tools: ['web'],
  };
  const input = { ...DEFAULTS, api_key: 'test-encrypted-key', runtime_config: overrides };
  validateParams('configure', input);
  validateParams('configure', { ...DEFAULTS, ...overrides });
  const prepared = store.prepare(input);
  store.save(prepared);
  const saved = JSON.parse(fs.readFileSync(store.file, 'utf8'));
  assert.deepEqual(saved.config, { ...DEFAULTS, ...overrides });
  assert.deepEqual(prepared.backend, { ...saved.config, api_key: input.api_key });
  const restarted = new SettingsStore(directory, encrypted);
  restarted.load();
  assert.deepEqual(restarted.backendConfig(), prepared.backend);
  const basicEdit = restarted.prepare({ ...DEFAULTS, model: 'changed-model' });
  assert.deepEqual(basicEdit.config, { ...saved.config, model: 'changed-model' });
  assert.throws(() => restarted.prepare({ ...DEFAULTS, runtime_config: ['read'] }), /JSON 对象/);
  assert.throws(() => restarted.prepare({ ...DEFAULTS, runtime_config: { api_key: 'must-not-leak' } }), /不支持.*api_key/);
  const replacement = restarted.prepare({ ...DEFAULTS, runtime_config: { max_retries: 3 } });
  restarted.save(replacement);
  assert.deepEqual(replacement.config, { ...DEFAULTS, max_retries: 3 });
  assert.equal(restarted.backendConfig().api_key, input.api_key);
  assert.deepEqual(restarted.prepare({ ...DEFAULTS, runtime_config: {} }).config, DEFAULTS);
});
test('encryption failure refuses to persist plaintext', () => {
  const store = new SettingsStore(path.join(os.tmpdir(), 'thinkflow-unused'), { isEncryptionAvailable: () => false });
  assert.throws(() => store.prepare({ ...DEFAULTS, api_key: 'fake-key' }), /加密不可用/);
});
test('a damaged saved key can be explicitly cleared or replaced without decryption', () => {
  const store = new SettingsStore(path.join(os.tmpdir(), 'thinkflow-unused'), { ...encrypted, decryptString: () => { throw new Error('damaged'); } });
  store.current.encrypted_key = 'broken-ciphertext';
  assert.throws(() => store.prepare({ ...DEFAULTS, api_key: '' }), /无法解锁/);
  assert.equal(store.prepare({ ...DEFAULTS, clear_api_key: true }).backend.api_key, '');
  assert.equal(store.prepare({ ...DEFAULTS, api_key: 'replacement-fake' }).backend.api_key, 'replacement-fake');
});
test('config rejects credentials, foreign protocols, path overrides, and invalid bounds', () => {
  for (const base_url of ['file:///tmp', 'https://user:pass@example.com', 'https://example.com/?key=secret']) assert.throws(() => normalizeConfig({ ...DEFAULTS, base_url }));
  for (const api_path of ['//evil.test/x', 'v1/messages', '/v1/messages?key=secret']) assert.throws(() => normalizeConfig({ ...DEFAULTS, api_path }));
  for (const max_run_turns of [0, -1, 1001, 1.5]) assert.throws(() => normalizeConfig({ ...DEFAULTS, max_run_turns }));
  assert.equal(normalizeConfig({ ...DEFAULTS, api_key: 'never-return', data_dir: 'ignored' }).api_key, undefined);
});
