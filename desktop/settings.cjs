const fs = require('node:fs');
const path = require('node:path');

const DEFAULTS = Object.freeze({ provider: 'openai', base_url: '', api_path: '', model: '', max_tokens: 16384, max_run_turns: 40, max_run_seconds: 1800, security_profile: 'balanced', thinking_mode: 'disabled', reasoning_effort: 'high', stream_options_include_usage: true });
function normalizeConfig(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('设置格式不正确');
  const config = {};
  for (const key of ['provider', 'base_url', 'api_path', 'model', 'security_profile']) {
    if (typeof input[key] !== 'string') throw new Error('设置字段不完整');
    config[key] = input[key].trim();
  }
  if (!['openai', 'anthropic'].includes(config.provider)) throw new Error('请选择支持的接口类型');
  if (!['balanced', 'read-only', 'open'].includes(config.security_profile)) throw new Error('权限策略无效');
  config.thinking_mode = input.thinking_mode ?? 'disabled';
  config.reasoning_effort = input.reasoning_effort ?? 'high';
  config.stream_options_include_usage = input.stream_options_include_usage !== false;
  if (!['disabled', 'enabled'].includes(config.thinking_mode) || !['low', 'high', 'max'].includes(config.reasoning_effort)) throw new Error('思考设置无效');
  if (config.base_url) {
    let url;
    try { url = new URL(config.base_url); } catch { throw new Error('端点地址必须是完整 URL'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('端点地址须为 HTTP/HTTPS，且不能包含凭据、查询或片段');
  }
  if (config.api_path && (!config.api_path.startsWith('/') || config.api_path.startsWith('//') || /[?#]/.test(config.api_path))) throw new Error('接口路径须以单个 / 开头且不包含查询参数');
  for (const [key, max] of [['max_tokens', 1000000], ['max_run_turns', 1000], ['max_run_seconds', 86400]]) {
    const value = Number(input[key]);
    if (!Number.isInteger(value) || value < 1 || value > max) throw new Error('运行限制必须为有效正整数');
    config[key] = value;
  }
  if (config.model.length > 512 || config.base_url.length > 4096 || config.api_path.length > 1024) throw new Error('设置字段过长');
  return config;
}
class SettingsStore {
  constructor(directory, safeStorage) { this.file = path.join(directory, 'settings.json'); this.safeStorage = safeStorage; this.current = { config: { ...DEFAULTS }, encrypted_key: '' }; }
  load() {
    if (!fs.existsSync(this.file)) return;
    try {
      const data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      this.current = { config: normalizeConfig(data.config), encrypted_key: typeof data.encrypted_key === 'string' ? data.encrypted_key : '' };
    } catch { throw new Error('本地设置无法读取，请检查用户数据目录中的 settings.json'); }
  }
  backendConfig() {
    const config = { ...this.current.config, api_key: '' };
    if (this.current.encrypted_key) {
      if (!this.safeStorage.isEncryptionAvailable()) throw new Error('Windows 凭据加密不可用，无法解锁已保存的密钥');
      try { config.api_key = this.safeStorage.decryptString(Buffer.from(this.current.encrypted_key, 'base64')); }
      catch { throw new Error('已保存的密钥无法解锁，请重新设置密钥'); }
    }
    return config;
  }
  prepare(input) {
    const config = normalizeConfig(input);
    let encrypted_key = this.current.encrypted_key;
    let api_key;
    if (input.clear_api_key === true) { encrypted_key = ''; api_key = ''; }
    else if (typeof input.api_key === 'string' && input.api_key.trim()) {
      if (!this.safeStorage.isEncryptionAvailable()) throw new Error('Windows 凭据加密不可用，未保存密钥');
      api_key = input.api_key.trim();
      if (api_key.length > 16384) throw new Error('密钥过长');
      encrypted_key = this.safeStorage.encryptString(api_key).toString('base64');
    } else api_key = this.backendConfig().api_key;
    return { config, encrypted_key, backend: { ...config, api_key } };
  }
  save(prepared) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const target = { config: prepared.config, encrypted_key: prepared.encrypted_key };
    const temp = `${this.file}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(target, null, 2), { mode: 0o600 });
    fs.renameSync(temp, this.file);
    this.current = target;
  }
}
module.exports = { DEFAULTS, normalizeConfig, SettingsStore };
