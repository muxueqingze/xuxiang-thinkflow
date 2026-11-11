const fs = require('node:fs');
const path = require('node:path');

const DEFAULTS = Object.freeze({ provider: 'openai', base_url: '', api_path: '', model: '', max_tokens: null, max_run_turns: 40, max_run_seconds: 1800, security_profile: 'balanced', thinking_budget: 0, thinking_mode: 'disabled', reasoning_effort: 'high', stream_options_include_usage: true, enable_native_tools: false, use_builtin_system_prompt: true, system_prompt: '' });
const RUNTIME_CONFIG_FIELDS = Object.freeze([
  'compaction', 'context', 'interfaces', 'tool_protocol', 'max_read_chars', 'max_retries',
  'retry_backoff_seconds', 'max_auto_continues', 'max_consecutive_failures', 'delivery_verify',
  'auto_verify_runnable_artifacts', 'max_delivery_fix_attempts', 'native_tools', 'disabled_native_tools',
]);
function runtimeConfig(config) {
  return Object.fromEntries(RUNTIME_CONFIG_FIELDS.filter(key => Object.hasOwn(config, key)).map(key => [key, config[key]]));
}
function validateRuntimeConfig(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('更多运行配置须为 JSON 对象');
  const unsupported = Object.keys(value).filter(key => !RUNTIME_CONFIG_FIELDS.includes(key));
  if (unsupported.length) throw new Error(`更多运行配置不支持以下字段：${unsupported.join('、')}`);
  return value;
}
function normalizeConfig(input, runtime = null) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('设置格式不正确');
  const config = {};
  for (const key of ['provider', 'base_url', 'api_path', 'model', 'security_profile']) {
    if (typeof input[key] !== 'string') throw new Error('设置字段不完整');
    config[key] = input[key].trim();
  }
  if (!['openai', 'anthropic'].includes(config.provider)) throw new Error('请选择支持的接口类型');
  if (!['balanced', 'read-only', 'open'].includes(config.security_profile)) throw new Error('权限策略无效');
  config.thinking_mode = input.thinking_mode ?? 'disabled';
  config.thinking_budget = input.thinking_budget ?? 0;
  config.reasoning_effort = input.reasoning_effort ?? 'high';
  config.stream_options_include_usage = input.stream_options_include_usage !== false;
  config.enable_native_tools = input.enable_native_tools ?? false;
  if (typeof config.enable_native_tools !== 'boolean') throw new Error('原生工具设置须为布尔值');
  config.use_builtin_system_prompt = input.use_builtin_system_prompt ?? true;
  config.system_prompt = input.system_prompt ?? '';
  if (typeof config.use_builtin_system_prompt !== 'boolean' || typeof config.system_prompt !== 'string') throw new Error('工程指引设置格式不正确');
  if (!['disabled', 'enabled'].includes(config.thinking_mode) || !['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].includes(config.reasoning_effort)) throw new Error('思考设置无效');
  if (!Number.isInteger(config.thinking_budget) || config.thinking_budget < 0) throw new Error('思考预算须为非负整数');
  if (config.base_url) {
    let url;
    try { url = new URL(config.base_url); } catch { throw new Error('端点地址必须是完整 URL'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('端点地址须为 HTTP/HTTPS，且不能包含凭据、查询或片段');
  }
  if (config.api_path && (!config.api_path.startsWith('/') || config.api_path.startsWith('//') || /[?#]/.test(config.api_path))) throw new Error('接口路径须以单个 / 开头且不包含查询参数');
  for (const [key, max] of [['max_tokens', 1000000], ['max_run_turns', 1000], ['max_run_seconds', 86400]]) {
    if (key !== 'max_tokens' && (input[key] == null || input[key] === '')) {
      config[key] = null;
      continue;
    }
    if (key === 'max_tokens' && (input[key] == null || input[key] === '' || input[key] === 0)) {
      if (config.provider === 'anthropic') throw new Error('Anthropic 接口要求显式填写输出 token 上限');
      config[key] = null;
      continue;
    }
    const value = Number(input[key]);
    if (!Number.isFinite(value) || (key !== 'max_run_seconds' && !Number.isInteger(value))
        || value < (key === 'max_run_seconds' ? 0 : 1) || value > max) throw new Error('运行限制数值无效');
    config[key] = value;
  }
  if (config.model.length > 512 || config.base_url.length > 4096 || config.api_path.length > 1024) throw new Error('设置字段过长');
  if (config.provider === 'anthropic' && config.thinking_budget && (config.thinking_budget < 1024 || config.thinking_budget >= config.max_tokens)) throw new Error('Anthropic 思考预算须至少为 1024，并小于输出 token 上限');
  return { ...config, ...(runtime ?? runtimeConfig(input)) };
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
    const runtime = Object.hasOwn(input, 'runtime_config')
      ? validateRuntimeConfig(input.runtime_config)
      : { ...runtimeConfig(this.current.config), ...runtimeConfig(input) };
    const config = normalizeConfig(input, runtime);
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
module.exports = { DEFAULTS, RUNTIME_CONFIG_FIELDS, normalizeConfig, runtimeConfig, SettingsStore };
