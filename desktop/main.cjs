const { app, BrowserWindow, ipcMain, dialog, safeStorage, Menu, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { BackendClient } = require('./backend.cjs');
const { SettingsStore } = require('./settings.cjs');
const PUBLIC_METHODS = new Set(['get_state', 'configure', 'new_session', 'resume_session', 'fork_session', 'run', 'cancel', 'approve', 'compact', 'acknowledge_recovery']);
let window, backend, store, initialized, quitting = false, settingsWarning = '';
if (!app.isPackaged && process.env.THINKFLOW_TEST_USER_DATA) app.setPath('userData', path.resolve(process.env.THINKFLOW_TEST_USER_DATA));
const hasLock = app.requestSingleInstanceLock();
if (!hasLock) app.quit();
app.on('second-instance', () => { if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
function validateSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('请求来源无效');
}
function validateParams(method, params) {
  if (!params || typeof params !== 'object' || Array.isArray(params) || JSON.stringify(params).length > 1024 * 1024) throw new Error('请求参数无效或过大');
  const fields = { get_state: [], configure: ['provider', 'base_url', 'api_path', 'model', 'api_key', 'clear_api_key', 'max_tokens', 'max_run_turns', 'max_run_seconds', 'security_profile'], new_session: [], resume_session: ['session_id'], fork_session: [], run: ['prompt'], cancel: [], approve: ['request_id', 'approved'], compact: [], acknowledge_recovery: [] }[method];
  if (!fields || Object.keys(params).some(key => !fields.includes(key))) throw new Error('不支持的请求参数');
  if (method === 'run' && (typeof params.prompt !== 'string' || !params.prompt.trim() || params.prompt.length > 200000)) throw new Error('请输入有效任务，最多 20 万字符');
  if (method === 'resume_session' && (typeof params.session_id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(params.session_id))) throw new Error('会话标识无效');
  if (method === 'approve' && (typeof params.request_id !== 'string' || typeof params.approved !== 'boolean')) throw new Error('授权参数无效');
}
function cleanError(error) {
  let message = String(error?.message || '操作失败');
  const key = (() => { try { return store?.backendConfig().api_key; } catch { return ''; } })();
  if (key) message = message.split(key).join('[密钥已隐藏]');
  return message.replace(/(?:sk-|Bearer\s+)[A-Za-z0-9_.-]{8,}/gi, '[凭据已隐藏]').slice(0, 2000);
}
async function wrapped(event, action) {
  try { validateSender(event); await initialized; const result = await action(); if (result?.messages && settingsWarning) result.settings_warning = settingsWarning; return { ok: true, result }; }
  catch (error) { return { ok: false, error: cleanError(error) }; }
}
function registerIPC() {
  ipcMain.handle('thinkflow:copy', (event, text) => wrapped(event, async () => {
    if (typeof text !== 'string' || text.length > 2000000) throw new Error('复制内容无效或过大');
    clipboard.writeText(text);
    return { copied: true };
  }));
  ipcMain.handle('thinkflow:request', (event, method, params = {}) => wrapped(event, async () => {
    if (!PUBLIC_METHODS.has(method)) throw new Error('不支持的操作');
    validateParams(method, params);
    if (method !== 'configure') return backend.request(method, params);
    const prepared = store.prepare(params);
    let previous;
    try { previous = store.backendConfig(); } catch { previous = { ...store.current.config, api_key: '' }; }
    const result = await backend.request('configure', { ...prepared.backend, clear_api_key: params.clear_api_key === true });
    try { store.save(prepared); }
    catch { await backend.request('configure', { ...previous, clear_api_key: !previous.api_key }).catch(() => {}); throw new Error('设置未保存，请检查用户目录写入权限'); }
    settingsWarning = '';
    return result;
  }));
  ipcMain.handle('thinkflow:workspace', event => wrapped(event, async () => {
    const selected = await dialog.showOpenDialog(window, { title: '选择工作区', properties: ['openDirectory'] });
    if (selected.canceled) return null;
    return backend.request('open_workspace', { cwd: selected.filePaths[0] });
  }));
  ipcMain.handle('thinkflow:export', event => wrapped(event, async () => {
    const { markdown } = await backend.request('export_session');
    const selected = await dialog.showSaveDialog(window, { title: '导出会话', defaultPath: '续想会话.md', filters: [{ name: 'Markdown 文档', extensions: ['md'] }] });
    if (selected.canceled) return null;
    await fs.writeFile(selected.filePath, markdown, 'utf8');
    return { saved: true };
  }));
}
async function createWindow() {
  const root = path.resolve(__dirname, '..');
  store = new SettingsStore(app.getPath('userData'), safeStorage);
  try { store.load(); } catch (error) { settingsWarning = error.message; }
  const environment = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
  for (const key of Object.keys(environment)) if (/^THINKFLOW_/.test(key) || /^(OPENAI|ANTHROPIC)_API_KEY$/.test(key)) delete environment[key];
  const command = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'thinkflow-service.exe') : (process.env.THINKFLOW_PYTHON || 'python');
  const args = app.isPackaged ? [] : ['-u', '-m', 'src.desktop_service'];
  backend = new BackendClient(command, args, { cwd: app.isPackaged ? process.resourcesPath : root, env: environment });
  initialized = (async () => {
    let config;
    try { config = store.backendConfig(); }
    catch (error) { settingsWarning = `${error.message}。请在设置中替换或明确清除密钥。`; config = { ...store.current.config, api_key: '' }; }
    return backend.request('initialize', { data_dir: path.join(app.getPath('userData'), 'data'), config });
  })();
  // Retain rejection for RPC callers without creating an unhandled rejection before the UI loads.
  initialized.catch(() => {});
  window = new BrowserWindow({ width: 1380, height: 900, minWidth: 1000, minHeight: 700, title: '续想 ThinkFlow', backgroundColor: '#f5f4f0', show: false, webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true } });
  Menu.setApplicationMenu(null);
  const entry = pathToFileURL(path.join(__dirname, 'renderer', 'index.html')).href;
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-redirect', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  backend.on('event', event => { if (event.type === 'state' && event.state && settingsWarning) event.state.settings_warning = settingsWarning; if (!window.isDestroyed()) window.webContents.send('thinkflow:event', event); });
  window.once('ready-to-show', () => window.show());
  registerIPC();
  await window.loadURL(entry);
}
if (hasLock) app.whenReady().then(createWindow).catch(() => { dialog.showErrorBox('续想未能启动', '无法创建桌面应用。请检查安装包是否完整。'); app.quit(); });
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting || !backend) return;
  event.preventDefault(); quitting = true;
  backend.shutdown().finally(() => app.quit());
});
