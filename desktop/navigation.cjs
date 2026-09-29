const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const SESSION_ID = /^[a-f0-9]{32}$/;
function normalizeNavigation(data) {
  if (!data || data.version !== 1 || !Array.isArray(data.workspaces) || data.workspaces.length > 20 || typeof data.last_workspace !== 'string') throw new Error('导航格式不正确');
  const seen = new Set();
  const workspaces = data.workspaces.map(item => {
    if (!item || typeof item.path !== 'string' || item.path.length > 32768 || !path.isAbsolute(item.path) || typeof item.last_session_id !== 'string' || (item.last_session_id && !SESSION_ID.test(item.last_session_id))) throw new Error('导航字段无效');
    const directory = path.resolve(item.path);
    const key = process.platform === 'win32' ? directory.toLowerCase() : directory;
    if (seen.has(key)) throw new Error('导航目录重复');
    seen.add(key);
    return { path: directory, last_session_id: item.last_session_id };
  });
  if (data.last_workspace && !workspaces.some(item => item.path === data.last_workspace)) throw new Error('上次工作区不在历史中');
  return { version: 1, last_workspace: data.last_workspace, workspaces };
}

class NavigationStore {
  constructor(directory) {
    this.file = path.join(directory, 'navigation.json');
    this.current = { version: 1, last_workspace: '', workspaces: [] };
    this.loadFailed = false;
    this.recoveryBackup = '';
  }
  load() {
    if (!fs.existsSync(this.file)) return;
    try { this.current = normalizeNavigation(JSON.parse(fs.readFileSync(this.file, 'utf8'))); }
    catch { this.loadFailed = true; throw new Error('本地工作区历史无法读取，navigation.json 原文件已保留；请选择工作区继续。'); }
  }
  registered(directory) {
    if (typeof directory !== 'string' || !path.isAbsolute(directory)) return null;
    const canonical = path.resolve(directory);
    return this.current.workspaces.find(item => process.platform === 'win32' ? item.path.toLowerCase() === canonical.toLowerCase() : item.path === canonical) || null;
  }
  recent() { return this.current.workspaces.map(item => ({ path: item.path, name: path.basename(item.path) || item.path })); }
  restoreCandidates() {
    const last = this.registered(this.current.last_workspace);
    return last ? [last, ...this.current.workspaces.filter(item => item !== last)] : [...this.current.workspaces];
  }
  remember(state) {
    if (!state?.cwd || !path.isAbsolute(state.cwd) || !SESSION_ID.test(state.session_id)) return;
    const existing = this.registered(state.cwd);
    const directory = path.resolve(state.cwd);
    this.current = { version: 1, last_workspace: directory, workspaces: [{ path: directory, last_session_id: state.session_id }, ...this.current.workspaces.filter(item => item !== existing)].slice(0, 20) };
    if (this.loadFailed && !this.recoveryBackup) {
      this.recoveryBackup = `${this.file}.corrupt-${Date.now()}-${randomUUID()}.bak`;
      try { fs.copyFileSync(this.file, this.recoveryBackup, fs.constants.COPYFILE_EXCL); }
      catch { this.recoveryBackup = ''; throw new Error('工作区历史仅在本次运行保留；无法备份损坏的 navigation.json，原文件未被覆盖。'); }
    }
    this.save();
    this.loadFailed = false;
  }
  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temp = `${this.file}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(this.current, null, 2), { mode: 0o600 });
    // Validate the file written by this process before replacing persisted history.
    normalizeNavigation(JSON.parse(fs.readFileSync(temp, 'utf8')));
    fs.renameSync(temp, this.file);
  }
}

async function openWorkspace(backend, navigation, directory) {
  const entry = navigation.registered(directory);
  return backend.request('open_workspace', { cwd: directory, ...(entry?.last_session_id ? { session_id: entry.last_session_id } : {}) });
}

async function openRecentWorkspace(backend, navigation, directory) {
  const entry = navigation.registered(directory);
  if (!entry) throw new Error('该目录不在最近工作区记录中，请使用选择工作区。');
  return openWorkspace(backend, navigation, entry.path);
}

async function restoreWorkspace(backend, navigation) {
  const warnings = [];
  for (const entry of navigation.restoreCandidates()) {
    try {
      if (!fs.statSync(entry.path).isDirectory()) throw new Error('不是目录');
    } catch { warnings.push('上次工作区已不存在或无法访问，已跳过。'); continue; }
    try { return { state: await openWorkspace(backend, navigation, entry.path), warning: warnings.join(' ') }; }
    catch { warnings.push('上次工作区无法恢复，原会话仍保留在本机。'); }
  }
  return { state: null, warning: [...new Set(warnings)].join(' ') };
}

module.exports = { NavigationStore, normalizeNavigation, openWorkspace, openRecentWorkspace, restoreWorkspace };
