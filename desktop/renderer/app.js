'use strict';
const $ = id => document.getElementById(id);
const api = window.thinkflow;
let state = { config: {}, messages: [], sessions: [], ledger: [], usage: {}, context: {} };
let connected = false, busy = false, renderTimer, refreshTimer, toastTimer, lastErrorSeen = '';
const activeTools = new Map();
const policyNames = { balanced: '平衡权限', 'read-only': '只读权限', open: '开放权限' };
const policies = {
  balanced: '文件工具限制在工作区内，并拦截常见敏感文件；命令等高风险操作需要逐次授权。',
  'read-only': '可以读取和检索工作区，阻止文件写入与命令执行。联网与其他工具仍遵循各自规则。',
  open: '放宽工作区边界、敏感路径与命令限制。选择前请确认您信任当前任务和工作区。'
};
const number = value => new Intl.NumberFormat('zh-CN').format(Number(value) || 0);
const running = () => ['running', 'approval'].includes(state.status);
const canRun = () => connected && state.cwd && state.config.base_url && state.config.model && !state.recovery_required && !state.settings_warning;
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function showError(message) { $('error-text').textContent = message; $('error-banner').hidden = false; }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500); }
async function perform(action, { notify, settings = false } = {}) {
  if (busy) return null;
  busy = true; updateControls();
  try {
    const result = await action();
    if (result?.messages && result?.config) applyState(result);
    if (notify) toast(notify);
    return result;
  } catch (error) {
    if (settings) { $('settings-error').textContent = error.message; $('settings-error').hidden = false; }
    else showError(error.message);
    return null;
  } finally { busy = false; updateControls(); }
}
function applyState(next) {
  if (!next.last_error && !next.settings_warning &&
      (next.session_id !== state.session_id || !['running', 'approval'].includes(next.status))) {
    $('error-banner').hidden = true;
    $('error-text').textContent = '';
  }
  state = next; connected = true;
  if (state.settings_warning) showError(state.settings_warning);
  if (!running()) activeTools.clear();
  if (state.last_error && state.last_error !== lastErrorSeen) showError(state.last_error);
  lastErrorSeen = state.last_error || '';
  render();
}
function updateControls() {
  const active = running();
  for (const id of ['new-session', 'workspace', 'setup-workspace', 'settings-top', 'settings-side', 'setup-model']) $(id).disabled = busy || active || !connected;
  for (const id of ['fork', 'compact', 'export']) $(id).disabled = busy || active || !connected || !state.session_id || (id === 'fork' && state.recovery_required);
  $('send').disabled = busy || active || !canRun() || !$('prompt').value.trim();
  $('send').hidden = active;
  $('stop').hidden = !active;
  $('stop').disabled = busy || !connected;
  $('prompt').disabled = !connected;
  $('approve').disabled = busy || !connected;
  $('reject').disabled = busy || !connected;
  $('acknowledge-recovery').disabled = busy || active || !connected;
  $('save-settings').disabled = busy;
  $('close-settings').disabled = busy;
  $('cancel-settings').disabled = busy;
  document.querySelectorAll('.session-button').forEach(button => { button.disabled = busy || active || !connected; });
  const hint = active ? '正在运行 · 可停止当前任务' : state.recovery_required ? '请先核对上次中断的操作' : !state.cwd ? '请先选择工作区' : !state.config.base_url || !state.config.model ? '请先在设置中连接模型端点' : 'Ctrl + Enter 发送 · Enter 换行';
  $('composer-hint').textContent = hint;
}
function render() {
  const config = state.config || {};
  $('connection-dot').className = `connection-dot ${connected ? 'connected' : 'failed'}`;
  $('connection-label').textContent = connected ? '本地后端已连接' : '本地后端连接断开';
  const session = (state.sessions || []).find(item => item.id === state.session_id);
  $('session-caption').textContent = state.session_id ? `会话 · ${state.session_id.slice(0, 8)}` : '新的会话';
  $('session-title').textContent = session?.title && session.title !== '新会话' ? session.title : '开始续想';
  $('session-title').title = $('session-title').textContent;
  const statusLabels = { idle: '准备就绪', running: '正在生成与执行', approval: '等待授权', error: '本次运行出现错误', cancelled: '已停止' };
  $('run-status').textContent = connected ? statusLabels[state.status] || '准备就绪' : '后端已断开';
  $('status-label').className = `status-label ${state.status || ''}`;
  $('model-label').textContent = config.model || '尚未配置模型';
  $('model-label').title = config.model || '';
  const directory = state.cwd?.split(/[\\/]/).filter(Boolean).pop();
  $('workspace-name').textContent = directory || '尚未选择';
  $('workspace-path').textContent = state.cwd || '文件操作将在您选择的目录内进行。';
  $('workspace').title = state.cwd ? `切换工作区：${state.cwd}` : '选择工作区';
  $('setup-workspace-detail').textContent = state.cwd || '指定本次任务可以访问的目录';
  $('setup-workspace-action').textContent = state.cwd ? '更换 →' : '选择 →';
  $('setup-model-detail').textContent = config.model && config.base_url ? `${config.model} · ${config.provider === 'anthropic' ? 'Anthropic' : 'OpenAI'} 兼容` : '配置兼容接口、模型和密钥';
  $('setup-model-action').textContent = config.model && config.base_url ? '修改 →' : '设置 →';
  const context = state.context || {};
  $('context-label').textContent = `上下文 ${number(context.messages)} 条 · ${number(context.chars)} 字符${context.compactions ? ` · 已压缩 ${number(context.compactions)} 次` : ''}`;
  $('policy-label').textContent = policyNames[config.security_profile] || '平衡权限';
  renderSessions(); renderMessages(); renderLedger(); renderApproval();
  const usage = state.usage || {};
  for (const [id, key] of [['usage-calls', 'api_calls'], ['usage-commands', 'commands'], ['usage-input', 'prompt_tokens'], ['usage-output', 'completion_tokens'], ['usage-saved', 'estimated_saved_api_calls']]) $(id).textContent = number(usage[key]);
  const usageReported = usage.reported === true || (usage.reported === undefined && (usage.prompt_tokens > 0 || usage.completion_tokens > 0));
  if (!usageReported) { $('usage-input').textContent = '—'; $('usage-output').textContent = '—'; }
  document.querySelector('.usage-note').textContent = `${!usageReported && usage.api_calls ? '端点未返回用量。' : ''}按成功的可预测工具调用估算，并非账单或实测成本。`;
  $('recovery').hidden = !state.recovery_required;
  $('recovery-details').textContent = (state.recovery || []).map(item => `${item.tool || '未知工具'} · ${item.id || '无编号'}\n${item.cmd || item.input_summary || item.path || '请核对工作区实际状态'}${item.input_hash ? `\n输入摘要 ${item.input_hash}` : ''}`).join('\n\n');
  if (state.transcript_window?.total > state.transcript_window?.shown) $('context-label').textContent += ` · 显示最近 ${state.transcript_window.shown} 条；完整记录在本机，导出上限1600万字符`;
  updateControls();
}
function renderSessions() {
  const list = $('session-list'); list.replaceChildren();
  $('session-count').textContent = (state.sessions || []).length;
  if (!state.sessions?.length) { list.append(element('p', 'muted side-empty', state.cwd ? '新会话将在这里保存' : '选择工作区后查看历史会话')); return; }
  for (const item of state.sessions) {
    const button = element('button', `session-button${item.id === state.session_id ? ' active' : ''}`);
    button.dataset.sessionId = item.id;
    button.setAttribute('aria-current', item.id === state.session_id ? 'true' : 'false');
    button.title = item.title || '新会话';
    button.append(element('strong', '', item.title || '新会话'));
    const timestamp = typeof item.updated_at === 'number' ? item.updated_at * 1000 : item.updated_at;
    const date = new Date(timestamp);
    button.append(element('small', '', Number.isNaN(date.getTime()) ? '本地会话' : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)));
    button.addEventListener('click', () => perform(() => api.request('resume_session', { session_id: item.id })));
    list.append(button);
  }
}
function inlineText(parent, text) {
  // Render an intentionally small Markdown subset without ever parsing raw HTML.
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let position = 0, match;
  while ((match = pattern.exec(text))) {
    parent.append(document.createTextNode(text.slice(position, match.index)));
    if (match[0].startsWith('`')) parent.append(element('code', '', match[0].slice(1, -1)));
    else parent.append(element('strong', '', match[0].slice(2, -2)));
    position = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(position)));
}
function renderContent(container, content) {
  const blocks = String(content || '').split(/(```[^\n]*\n[\s\S]*?(?:\n```|$))/g);
  for (const block of blocks) {
    if (!block) continue;
    if (block.startsWith('```')) {
      const pre = element('pre'); pre.append(element('code', '', block.replace(/^```[^\n]*\n/, '').replace(/\n```$/, ''))); container.append(pre); continue;
    }
    const paragraphs = block.split(/\n\s*\n/);
    for (const paragraph of paragraphs) {
      if (!paragraph.trim()) continue;
      if (/^#{1,6}\s/.test(paragraph) && !paragraph.trim().includes('\n')) {
        const heading = element('div', 'markdown-heading'); inlineText(heading, paragraph.replace(/^#{1,6}\s/, '')); container.append(heading);
      } else if (paragraph.split('\n').every(line => /^\s*(?:[-*]|\d+\.)\s/.test(line))) {
        const list = element('ul', 'markdown-list');
        for (const line of paragraph.split('\n')) { const li = element('li'); inlineText(li, line.replace(/^\s*(?:[-*]|\d+\.)\s/, '')); list.append(li); }
        container.append(list);
      } else { const text = element('div', 'markdown-paragraph'); inlineText(text, paragraph); container.append(text); }
    }
  }
}
function renderMessages() {
  const scroller = $('messages-scroll');
  const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 96;
  const list = $('message-list'); list.replaceChildren();
  const messages = state.messages || [];
  $('welcome').hidden = messages.length > 0;
  messages.forEach((message, index) => {
    const isUser = message.role === 'user';
    const article = element('article', `message ${isUser ? 'user' : 'assistant'}`);
    const header = element('div', 'message-header');
    header.append(element('span', 'message-avatar', isUser ? '您' : '续'), element('span', 'message-role', isUser ? '您' : message.role === 'tool' ? '工具结果' : '续想'));
    const copy = element('button', 'message-copy', '复制'); copy.title = '复制此消息';
    copy.addEventListener('click', async () => { try { await api.copyText(String(message.content || '')); toast('已复制消息'); } catch { showError('无法写入剪贴板，请选择文本后复制'); } });
    header.append(copy); article.append(header);
    const body = element('div', 'message-body');
    if (isUser) body.textContent = String(message.content || ''); else renderContent(body, message.content);
    article.append(body);
    if (!isUser && running() && index === messages.length - 1) article.append(element('div', 'stream-label', state.status === 'approval' ? '等待本次操作授权' : '正在输出…'));
    list.append(article);
  });
  if (atBottom) scroller.scrollTop = scroller.scrollHeight;
}
function renderLedger() {
  const list = $('ledger-list');
  const openEntries = new Set([...list.querySelectorAll('details[open]')].map(node => node.dataset.entry));
  list.replaceChildren();
  const ledger = [...(state.ledger || [])];
  for (const item of activeTools.values()) if (!ledger.some(record => String(record.id) === String(item.id) && record.tool === item.tool)) ledger.push(item);
  $('ledger-count').textContent = number(ledger.length);
  if (!ledger.length) {
    const empty = element('div', 'ledger-empty'); empty.append(element('span', 'empty-ledger-mark', '≡'), element('h3', '', '尚无工具执行'), element('p', '', '文件读写、命令和联网操作将在这里逐条记录。')); list.append(empty); return;
  }
  const statuses = { success: '成功', failed: '失败', pending: '排队', running: '执行中', approval: '等待授权', cancelled: '已停止', denied: '已拒绝', error: '错误', skipped: '已跳过' };
  const flows = { delayed: '持续执行', blocking: '等待结果', confirm: '需授权' };
  const risks = { low: '低风险', medium: '中风险', high: '高风险' };
  ledger.forEach((item, index) => {
    const entry = element('details', 'ledger-entry'); entry.dataset.entry = `${item.id}-${index}`; entry.open = openEntries.has(entry.dataset.entry);
    const summary = element('summary'); const top = element('div', 'ledger-top');
    const visibleStatus = state.pending_approval?.tool === item.tool && item.status === 'running' ? 'approval' : item.status;
    const statusClass = ['failed', 'error', 'denied'].includes(visibleStatus) ? 'failed' : ['pending', 'running', 'approval'].includes(visibleStatus) ? 'pending' : '';
    top.append(element('span', 'ledger-tool', item.tool || '工具'), element('span', `ledger-status ${statusClass}`, statuses[visibleStatus] || visibleStatus || '待确认'), element('span', 'ledger-id', `#${item.id || index + 1}`));
    summary.append(top, element('div', 'ledger-path', item.path || item.output_summary || '展开查看执行详情'));
    if (item.dest) summary.append(element('div', 'ledger-path', `→ ${item.dest}`));
    const meta = element('div', 'ledger-meta');
    meta.append(element('span', '', flows[item.flow] || item.flow || '工具调用'), element('span', '', risks[item.risk] || item.risk || ''));
    summary.append(meta); entry.append(summary);
    const detail = [item.output_summary, item.error ? `错误：${item.error}` : '', item.bytes_written ? `写入 ${number(item.bytes_written)} 字节` : '', item.exit_code != null ? `退出码：${item.exit_code}` : ''].filter(Boolean).join('\n');
    entry.append(element('div', 'ledger-detail', detail || '没有额外执行详情')); list.append(entry);
  });
}
function renderApproval() {
  const pending = state.pending_approval;
  $('approval').hidden = !pending;
  if (pending) { $('approval-tool').textContent = pending.tool; const input = pending.input && typeof pending.input === 'object' ? Object.fromEntries(Object.entries(pending.input).filter(([, value]) => value !== null && value !== undefined)) : pending.input; $('approval-params').textContent = JSON.stringify(input, null, 2); }
}
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => { try { applyState(await api.request('get_state')); } catch (error) { showError(error.message); } }, 100);
}
function openSettings() {
  if (running() || busy || !connected) return;
  const form = $('settings-form');
  for (const name of ['provider', 'model', 'base_url', 'api_path', 'max_tokens', 'max_run_turns', 'max_run_seconds', 'security_profile']) form.elements.namedItem(name).value = state.config[name] ?? '';
  form.elements.namedItem('api_key').value = '';
  form.elements.namedItem('api_key').disabled = false;
  form.elements.namedItem('clear_api_key').checked = false;
  $('key-state').textContent = state.settings_warning ? '未解锁 · 请替换或清除密钥' : state.config.has_api_key ? '已加密保存 · 留空保留' : '尚未保存';
  $('settings-error').hidden = true;
  updatePolicy(); $('settings-dialog').showModal();
}
function closeSettings() { if (!busy) { $('settings-dialog').close(); $('settings-form').elements.namedItem('api_key').value = ''; } }
function updatePolicy() { $('policy-description').textContent = policies[$('settings-form').elements.namedItem('security_profile').value]; }
async function chooseWorkspace() { await perform(() => api.chooseWorkspace()); }
for (const id of ['workspace', 'setup-workspace']) $(id).addEventListener('click', chooseWorkspace);
for (const id of ['settings-top', 'settings-side', 'setup-model']) $(id).addEventListener('click', openSettings);
for (const id of ['close-settings', 'cancel-settings']) $(id).addEventListener('click', closeSettings);
$('settings-dialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); else $('settings-form').elements.namedItem('api_key').value = ''; });
$('settings-form').elements.namedItem('security_profile').addEventListener('change', updatePolicy);
$('settings-form').elements.namedItem('clear_api_key').addEventListener('change', event => { $('settings-form').elements.namedItem('api_key').disabled = event.target.checked; });
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault(); $('settings-error').hidden = true;
  const values = Object.fromEntries(new FormData(event.currentTarget));
  for (const field of ['max_tokens', 'max_run_turns', 'max_run_seconds']) values[field] = Number(values[field]);
  values.clear_api_key = event.currentTarget.elements.namedItem('clear_api_key').checked;
  const result = await perform(() => api.request('configure', values), { settings: true });
  if (result) { closeSettings(); $('error-banner').hidden = true; toast('接口与权限设置已保存'); }
});
$('new-session').addEventListener('click', () => perform(() => api.request('new_session')));
$('fork').addEventListener('click', () => perform(() => api.request('fork_session'), { notify: '已从当前会话创建分支' }));
$('compact').addEventListener('click', () => perform(() => api.request('compact'), { notify: '上下文压缩已处理' }));
$('export').addEventListener('click', async () => { const result = await perform(() => api.exportDialog()); if (result?.saved) toast('会话已导出'); });
$('toggle-ledger').addEventListener('click', () => {
  const hidden = $('workbench').classList.toggle('ledger-hidden');
  $('toggle-ledger').textContent = hidden ? '显示账本' : '收起账本';
  $('toggle-ledger').setAttribute('aria-expanded', String(!hidden));
});
$('dismiss-error').addEventListener('click', () => { $('error-banner').hidden = true; });
$('prompt').addEventListener('input', updateControls);
$('composer').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || running() || !canRun() || !$('prompt').value.trim()) return;
  const prompt = $('prompt').value;
  $('error-banner').hidden = true;
  const result = await perform(() => api.request('run', { prompt }));
  if (result?.started) { $('prompt').value = ''; updateControls(); scheduleRefresh(); }
});
$('stop').addEventListener('click', () => perform(() => api.request('cancel'), { notify: '已停止当前运行' }));
for (const [id, approved] of [['approve', true], ['reject', false]]) $(id).addEventListener('click', async () => {
  const request_id = state.pending_approval?.request_id;
  if (!request_id) return;
  const result = await perform(() => api.request('approve', { request_id, approved }));
  if (result?.accepted) { state.pending_approval = null; renderApproval(); scheduleRefresh(); }
});
$('acknowledge-recovery').addEventListener('click', async () => { const result = await perform(() => api.request('acknowledge_recovery'), { notify: '已确认恢复；旧操作不会自动重放' }); if (result) $('error-banner').hidden = true; });
document.addEventListener('keydown', event => {
  if (event.ctrlKey && event.key === 'Enter' && !$('settings-dialog').open) { event.preventDefault(); $('composer').requestSubmit(); }
  if (event.ctrlKey && event.key.toLowerCase() === 'n' && !$('settings-dialog').open && !$('new-session').disabled) { event.preventDefault(); $('new-session').click(); }
});
api.onEvent(event => {
  if (event.type === 'state' && event.state) { clearTimeout(renderTimer); renderTimer = null; applyState(event.state); }
  else if (event.type === 'text_delta') {
    state.messages ||= [];
    if (state.messages.at(-1)?.role !== 'assistant') state.messages.push({ role: 'assistant', content: '' });
    state.messages.at(-1).content += event.text || '';
    if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; renderMessages(); }, 64);
  } else if (event.type === 'backend_error') { connected = false; state.status = 'error'; state.pending_approval = null; activeTools.clear(); render(); showError(`${event.message}。请关闭并重新启动续想。`); }
  else if (event.type === 'error') showError(event.message || event.error || '本次运行发生错误，请查看执行账本。');
  else if (event.type === 'approval_required') { state.pending_approval = event; state.status = 'approval'; renderApproval(); updateControls(); scheduleRefresh(); }
  else if (event.type === 'tool_started') { activeTools.set(`${event.channel}:${event.id}`, { ...event, status: 'running' }); renderLedger(); scheduleRefresh(); }
  else if (event.type === 'tool_completed') { activeTools.delete(`${event.channel}:${event.id}`); scheduleRefresh(); }
  else if (event.type === 'run_finished') scheduleRefresh();
});
updateControls();
api.request('get_state').then(applyState).catch(error => { connected = false; render(); showError(error.message); });
