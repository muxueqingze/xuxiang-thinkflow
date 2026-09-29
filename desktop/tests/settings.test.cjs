const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { SettingsStore, DEFAULTS, normalizeConfig } = require('../settings.cjs');
const encrypted = { isEncryptionAvailable: () => true, encryptString: text => Buffer.from(text.split('').reverse().join('')), decryptString: bytes => bytes.toString().split('').reverse().join('') };
test('only ciphertext persists; blank preserves and explicit clear removes a key', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'thinkflow-settings-'));
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
