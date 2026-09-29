const { app, BrowserWindow, ipcMain, dialog, safeStorage, Menu, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { BackendClient } = require('./backend.cjs');
const { SettingsStore } = require('./settings.cjs');
const { NavigationStore, openWorkspace, openRecentWorkspace, restoreWorkspace } = require('./navigation.cjs');
const PUBLIC_METHODS = new Set(['get_state', 'configure', 'new_session', 'resume_session', 'fork_session', 'update_session', 'run', 'cancel', 'approve', 'compact', 'acknowledge_recovery', 'submit_input', 'get_input_receipt', 'update_input', 'resume_queue', 'pause_queue', 'workspace_files', 'read_workspace_file', 'workspace_changes', 'workspace_diff', 'revert_workspace_change', 'model_probe', 'model_catalog']);
let window, backend, store, navigation, initialized, quitting = false, settingsWarning = '', navigationWarning = '';
if (!app.isPackaged && process.env.THINKFLOW_TEST_USER_DATA) app.setPath('userData', path.resolve(process.env.THINKFLOW_TEST_USER_DATA));
const hasLock = app.requestSingleInstanceLock();
if (!hasLock) app.quit();
app.on('second-instance', () => { if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
function validateSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('请求来源无效');
}
function validateParams(method, params) {
  if (!params || typeof params !== 'object' || Array.isArray(params) || JSON.stringify(params).length > 1024 * 1024) throw new Error('请求参数无效或过大');
  const fields = { get_state: [], configure: ['provider', 'base_url', 'api_path', 'model', 'api_key', 'clear_api_key', 'max_tokens', 'max_run_turns', 'max_run_seconds', 'security_profile', 'thinking_mode', 'reasoning_effort', 'stream_options_include_usage'], new_session: [], resume_session: ['session_id'], fork_session: [], update_session: ['session_id', 'title', 'pinned', 'archived'], run: ['prompt'], cancel: [], approve: ['request_id', 'approved'], compact: [], acknowledge_recovery: [], submit_input: ['command_id', 'prompt', 'session_id', 'attachments'], get_input_receipt: ['command_id', 'session_id'], update_input: ['command_id', 'action', 'prompt'], resume_queue: [], pause_queue: [], workspace_files: ['path', 'query'], read_workspace_file: ['path'], workspace_changes: [], workspace_diff: ['change_id'], revert_workspace_change: ['change_id', 'expected_revision'], model_probe: [], model_catalog: [] }[method];
  if (!fields || Object.keys(params).some(key => !fields.includes(key))) throw new Error('不支持的请求参数');
  if (['run', 'submit_input'].includes(method) && (typeof params.prompt !== 'string' || !params.prompt.trim() || params.prompt.length > 200000)) throw new Error('请输入有效任务，最多 20 万字符');
  if (['submit_input', 'get_input_receipt', 'update_input'].includes(method) && (typeof params.command_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(params.command_id))) throw new Error('输入命令编号无效');
  if (['submit_input', 'get_input_receipt'].includes(method) && (typeof params.session_id !== 'string' || !/^[a-f0-9]{32}$/.test(params.session_id))) throw new Error('会话标识无效');
  if (method === 'submit_input' && params.attachments !== undefined && (!Array.isArray(params.attachments) || params.attachments.length > 8 || params.attachments.some(item => !item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).sort().join(',') !== 'path,revision' || typeof item.path !== 'string' || item.path.length > 32768 || typeof item.revision !== 'string' || item.revision.length > 128))) throw new Error('附件参数无效');
  if (method === 'update_input' && (!['remove', 'edit'].includes(params.action) || (params.action === 'edit' && (typeof params.prompt !== 'string' || !params.prompt.trim() || params.prompt.length > 200000)))) throw new Error('队列编辑参数无效');
  for (const field of ['path', 'query', 'change_id', 'expected_revision']) if (field in params && (typeof params[field] !== 'string' || params[field].length > (field === 'path' ? 32768 : 2048))) throw new Error('文件参数无效');
  if (method === 'read_workspace_file' && typeof params.path !== 'string') throw new Error('缺少文件路径');
  if (['workspace_diff', 'revert_workspace_change'].includes(method) && (typeof params.change_id !== 'string' || !params.change_id)) throw new Error('缺少变更编号');
  if (method === 'revert_workspace_change' && (typeof params.expected_revision !== 'string' || !/^[a-f0-9]{64}$/.test(params.expected_revision))) throw new Error('文件版本无效');
  if (['resume_session', 'update_session'].includes(method) && (typeof params.session_id !== 'string' || !/^[a-f0-9]{32}$/.test(params.session_id))) throw new Error('会话标识无效');
  if (method === 'update_session') {
    if ('title' in params && (typeof params.title !== 'string' || !params.title.trim() || params.title.trim().length > 120)) throw new Error('会话标题须为 1–120 字符');
    for (const name of ['pinned', 'archived']) if (name in params && typeof params[name] !== 'boolean') throw new Error('置顶和归档须为布尔值');
  }
  if (method === 'approve' && (typeof params.request_id !== 'string' || typeof params.approved !== 'boolean')) throw new Error('授权参数无效');
}
function cleanError(error) {
  let message = String(error?.message || '操作失败');
  const key = (() => { try { return store?.backendConfig().api_key; } catch { return ''; } })();
  if (key) message = message.split(key).join('[密钥已隐藏]');
  return message.replace(/(?:sk-|Bearer\s+)[A-Za-z0-9_.-]{8,}/gi, '[凭据已隐藏]').slice(0, 2000);
}
async function wrapped(event, action) {
  try { validateSender(event); await initialized; return { ok: true, result: decorateState(await action()) }; }
  catch (error) { return { ok: false, error: cleanError(error) }; }
}
function decorateState(state) {
  if (state?.state) state.state = decorateState(state.state);
  if (!state?.messages) return state;
  if (settingsWarning) state.settings_warning = settingsWarning;
  state.recent_workspaces = navigation.recent();
  state.navigation_warning = [navigationWarning, state.navigation_warning].filter(Boolean).join(' ');
  return state;
}
function rememberState(state) {
  try {
    navigation.remember(state);
    if (navigation.recoveryBackup) navigationWarning = '工作区历史已重新保存；损坏原件已保留为用户目录中的 navigation.json.corrupt-*.bak。';
  }
  catch (error) { navigationWarning = cleanError(error); }
  return state;
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
    if (method !== 'configure') {
      const result = await backend.request(method, params);
      if (['new_session', 'resume_session', 'fork_session'].includes(method)) rememberState(result);
      return result;
    }
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
    return rememberState(await openWorkspace(backend, navigation, selected.filePaths[0]));
  }));
  ipcMain.handle('thinkflow:recent-workspace', (event, directory) => wrapped(event, async () => rememberState(await openRecentWorkspace(backend, navigation, directory))));
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
  navigation = new NavigationStore(app.getPath('userData'));
  try { navigation.load(); } catch (error) { navigationWarning = error.message; }
  const environment = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
  for (const key of Object.keys(environment)) if (/^THINKFLOW_/.test(key) || /^(OPENAI|ANTHROPIC)_API_KEY$/.test(key)) delete environment[key];
  const command = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'thinkflow-service.exe') : (process.env.THINKFLOW_PYTHON || 'python');
  const args = app.isPackaged ? [] : ['-u', '-m', 'src.desktop_service'];
  backend = new BackendClient(command, args, { cwd: app.isPackaged ? process.resourcesPath : root, env: environment });
  initialized = (async () => {
    let config;
    try { config = store.backendConfig(); }
    catch (error) { settingsWarning = `${error.message}。请在设置中替换或明确清除密钥。`; config = { ...store.current.config, api_key: '' }; }
    const state = await backend.request('initialize', { data_dir: path.join(app.getPath('userData'), 'data'), config });
    const restored = await restoreWorkspace(backend, navigation);
    if (restored.warning) navigationWarning = [navigationWarning, restored.warning].filter(Boolean).join(' ');
    return restored.state ? rememberState(restored.state) : state;
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
  backend.on('event', event => { if (event.type === 'state' && event.state) decorateState(event.state); if (!window.isDestroyed()) window.webContents.send('thinkflow:event', event); });
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
