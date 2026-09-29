'use strict';
const $ = id => document.getElementById(id);
const api = window.thinkflow;
let state = { config: {}, messages: [], sessions: [], ledger: [], usage: {}, context: {} };
let connected = false, busy = false, renderTimer, refreshTimer, toastTimer, lastErrorSeen = '';
const activeTools = new Map();
let draftKey = '', queue = [], queuePaused = true, sending = false;
let composing = false, showArchived = false, messageContext = '', messageNodes = [], followBottom = true, localWarning = '';
let projectionStream = '', projectionSeq = -1, activityPhase = '', statusNotice = '', navigationGeneration = 0;
const retiredStreams = new Set(), localFallback = new Map();
const contextKey = source => JSON.stringify([source.cwd || '', source.session_id || 'new']);
const storagePrefix = 'thinkflow-v07:';
function readLocal(key, fallback) {
  if (localFallback.has(key)) return localFallback.get(key);
  try { const value = localStorage.getItem(storagePrefix + key); return value === null ? fallback : JSON.parse(value); }
  catch { showError('本机草稿或待确认内容无法读取，原记录未覆盖。'); return fallback; }
}
function writeLocal(key, value) {
  if (key.startsWith('draft:')) localFallback.set(key, value);
  try { localStorage.setItem(storagePrefix + key, JSON.stringify(value)); return true; }
  catch { localWarning = '本机保存失败；请复制草稿或待确认内容后再关闭应用。'; showError(localWarning); return false; }
}
function saveDraft() { return !draftKey || writeLocal('draft:' + draftKey, $('prompt').value); }
function resizePrompt() { const prompt = $('prompt'); prompt.style.height = 'auto'; const limit = parseFloat(getComputedStyle(prompt).maxHeight) || 144; prompt.style.height = Math.min(prompt.scrollHeight, limit) + 'px'; }
function acceptProjection(source, snapshot = false) {
  if (!source.stream_id || !Number.isInteger(source.seq)) return true;
  if (retiredStreams.has(source.stream_id)) return false;
  if (source.stream_id !== projectionStream) {
    if (!snapshot && projectionStream) { scheduleRefresh(); return false; }
    if (projectionStream) retiredStreams.add(projectionStream);
    projectionStream = source.stream_id; projectionSeq = -1;
  }
  if (source.seq < projectionSeq || (!snapshot && source.seq === projectionSeq)) return false;
  if (!snapshot && projectionSeq >= 0 && source.seq > projectionSeq + 1) { scheduleRefresh(); return false; }
  if (!snapshot && source.session_id && source.session_id !== state.session_id) { scheduleRefresh(); return false; }
  projectionSeq = source.seq; return true;
}
const policyNames = { balanced: '平衡权限', 'read-only': '只读权限', open: '开放权限' };
const policies = {
  balanced: '文件工具限制在工作区内，并拦截常见敏感文件；命令等高风险操作需要逐次授权。',
  'read-only': '可以读取和检索工作区，阻止文件写入与命令执行。联网与其他工具仍遵循各自规则。',
  open: '放宽命令及敏感读取限制，可读取工作区之外的路径；带变更记录的文件写入仍限当前工作区。'
};
const number = value => new Intl.NumberFormat('zh-CN').format(Number(value) || 0);
const running = () => ['running', 'approval'].includes(state.status);
const currentSession = () => state.sessions?.find(item => item.id === state.session_id);
const canRun = () => connected && state.cwd && state.config.base_url && state.config.model && !state.recovery_required && !state.settings_warning && !currentSession()?.archived;
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function showError(message) { $('error-text').textContent = message; $('error-banner').hidden = false; }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500); }
async function perform(action, { notify, settings = false, navigation = false } = {}) {
  if (busy) return null;
  const generation = navigation ? ++navigationGeneration : navigationGeneration;
  busy = true; updateControls();
  try {
    const result = await action();
    if (generation === navigationGeneration) {
      if (result?.state?.messages) applyState(result.state, { navigation });
      else if (result?.messages && result?.config) applyState(result, { navigation });
    }
    if (notify) toast(notify);
    return result;
  } catch (error) {
    if (settings) { $('settings-error').textContent = error.message; $('settings-error').hidden = false; }
    else showError(error.message);
    return null;
  } finally { busy = false; updateControls(); }
}
function applyState(next, { navigation = false } = {}) {
  if (!navigation && projectionStream && next.stream_id === projectionStream && next.seq === projectionSeq && contextKey(next) !== contextKey(state)) return false;
  if (!acceptProjection(next, true)) return false;
  if (!next.last_error && !next.settings_warning &&
      (next.session_id !== state.session_id || !['running', 'approval'].includes(next.status))) {
    $('error-banner').hidden = true;
    $('error-text').textContent = '';
  }
  const nextKey = contextKey(next);
  if (draftKey !== nextKey) { saveDraft(); draftKey = nextKey; $('prompt').value = readLocal('draft:' + draftKey, ''); resizePrompt(); loadInputContext(); resetWorkspacePanel(); messageContext = ''; }
  state = next; connected = true; queue = state.input_queue || []; queuePaused = state.queue_paused === true;
  if (state.navigation_warning) showError(state.navigation_warning);
  if (localWarning) showError(localWarning);
  if (state.settings_warning) showError(state.settings_warning);
  if (!running()) { activeTools.clear(); activityPhase = ''; statusNotice = ''; }
  if (state.last_error && state.last_error !== lastErrorSeen) showError(state.last_error);
  lastErrorSeen = state.last_error || '';
  render();
}
function updateControls() {
  const active = running();
  for (const id of ['new-session', 'workspace', 'setup-workspace', 'settings-top', 'settings-side', 'setup-model']) $(id).disabled = busy || active || !connected;
  for (const id of ['fork', 'compact', 'export']) $(id).disabled = busy || active || !connected || !state.session_id || (id === 'fork' && state.recovery_required);
  $('send').disabled = busy || sending || !canRun() || !$('prompt').value.trim() || queue.filter(item => item.status !== 'running').length >= 20 || Boolean(pendingInput);
  $('send').hidden = false;
  $('send').textContent = active || queue.some(item => item.status !== 'running') ? '加入队列 ↑' : '发送 ↑';
  $('stop').hidden = !active;
  $('stop').disabled = busy || !connected;
  $('prompt').disabled = false;
  $('approve').disabled = busy || !connected;
  $('reject').disabled = busy || !connected;
  $('acknowledge-recovery').disabled = busy || active || !connected;
  $('save-settings').disabled = busy;
  $('close-settings').disabled = busy;
  $('cancel-settings').disabled = busy;
  document.querySelectorAll('.session-button, .session-menu-button, .recent-item').forEach(button => { button.disabled = busy || active || !connected; });
  for (const id of ['rename-current', 'pin-current', 'archive-current', 'recent-workspaces']) $(id).disabled = busy || active || !connected || (id !== 'recent-workspaces' && !state.session_id);
  $('policy-button').disabled = $('settings-top').disabled;
  $('resume-queue').disabled = busy || sending || !canRun();
  $('pause-queue').disabled = busy || !connected;
  updateWorkspaceControls();
  const hint = active ? 'Enter 加入队列 · 当前成功完成后继续' : state.recovery_required ? '请先核对上次中断的操作' : !state.cwd ? '请先选择工作区' : !state.config.base_url || !state.config.model ? '请先在设置中连接模型端点' : currentSession()?.archived ? '会话已归档，请先恢复' : 'Enter 发送 · Shift + Enter 换行';
  $('composer-hint').textContent = hint;
}
function render() {
  const config = state.config || {};
  $('connection-dot').className = `connection-dot ${connected ? 'connected' : 'failed'}`;
  $('connection-label').textContent = connected ? '本地后端已连接' : '本地后端连接断开';
  const session = (state.sessions || []).find(item => item.id === state.session_id);
  $('session-caption').textContent = state.session_id ? `会话 · ${state.session_id.slice(0, 8)}` : '新的会话';
  $('session-title').textContent = session?.title && session.title !== '新会话' ? session.title : '新会话';
  $('session-title').title = $('session-title').textContent;
  const statusLabels = { idle: state.recovery_required ? '等待核对上次中断' : queuePaused && queue.length ? '待发队列已暂停' : '准备就绪', running: activeTools.size ? '持续生成 · 工具正在执行' : '正在生成', approval: '等待您授权本次操作', error: '运行失败 · 后续任务已暂停', cancelled: '已停止 · 后续任务已保留' };
  if (activityPhase === 'waiting_feedback') statusLabels.running = '等待工具结果，随后继续生成';
  if (activityPhase === 'thinking') statusLabels.running = '正在思考';
  if (statusNotice && running()) statusLabels.running = statusNotice;
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
  $('archived-notice').hidden = !session?.archived;
  $('pin-current').textContent = session?.pinned ? '取消置顶' : '置顶会话';
  $('archive-current').textContent = session?.archived ? '恢复会话' : '归档会话';
  renderSessions(); renderMessages(); renderLedger(); renderApproval(); renderQueue(); renderAttachments(); renderInputReceipt(); renderTaskPlan();
  const usage = state.usage || {};
  for (const [id, key] of [['usage-calls', 'api_calls'], ['usage-commands', 'commands'], ['usage-input', 'prompt_tokens'], ['usage-output', 'completion_tokens'], ['usage-saved', 'estimated_saved_api_calls']]) $(id).textContent = number(usage[key]);
  const usageReported = usage.reported === true || (usage.reported === undefined && (usage.prompt_tokens > 0 || usage.completion_tokens > 0));
  if (!usageReported) {
    $('usage-input').textContent = usage.reported_turns ? `≥ ${number(usage.prompt_tokens)}` : '—';
    $('usage-output').textContent = usage.reported_turns ? `≥ ${number(usage.completion_tokens)}` : '—';
  }
  document.querySelector('.usage-note').textContent = `${!usageReported && usage.api_calls ? (usage.reported_turns ? '部分回合未返回用量，仅显示已知下限。' : '端点未返回用量。') : ''}节省调用为估算，并非账单或实测成本。`;
  $('recovery').hidden = !state.recovery_required;
  $('recovery-details').textContent = (state.recovery || []).map(item => `${item.tool || '未知工具'} · ${item.id || '无编号'}\n${item.cmd || item.input_summary || item.path || '请核对工作区实际状态'}${item.input_hash ? `\n输入摘要 ${item.input_hash}` : ''}`).join('\n\n');
  if (state.transcript_window?.total > state.transcript_window?.shown) $('context-label').textContent += ` · 显示最近 ${state.transcript_window.shown} 条；完整记录在本机，导出上限1600万字符`;
  updateControls();
  return true;
}
function renderSessions() {
  const list = $('session-list'); list.replaceChildren();
  const query = $('session-search').value.trim().toLowerCase();
  const sessions = (state.sessions || []).filter(item => (showArchived || !item.archived) && (!query || (item.title || '新会话').toLowerCase().includes(query))).sort((a,b) => Number(b.pinned) - Number(a.pinned));
  $('session-count').textContent = sessions.length;
  if (!sessions.length) { list.append(element('p', 'muted side-empty', query ? '没有匹配的会话' : state.cwd ? '新会话将在这里保存' : '选择工作区后查看历史会话')); return; }
  for (const item of sessions) {
    const row = element('div', `session-row${item.id === state.session_id ? ' active' : ''}`);
    const button = element('button', 'session-button');
    button.dataset.sessionId = item.id;
    button.setAttribute('aria-current', item.id === state.session_id ? 'true' : 'false');
    button.title = item.title || '新会话';
    button.append(element('strong', '', `${item.pinned ? '· ' : ''}${item.title || '新会话'}`));
    const date = new Date(typeof item.updated_at === 'number' ? item.updated_at * 1000 : item.updated_at);
    button.append(element('small', '', item.archived ? '已归档' : Number.isNaN(date.getTime()) ? '本地会话' : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date)));
    button.addEventListener('click', () => selectSession(item.id));
    const menu = element('button', 'session-menu-button', '···');
    menu.setAttribute('aria-label', `编辑会话：${item.title || '新会话'}`);
    menu.addEventListener('click', async () => { if (item.id !== state.session_id) { const result = await selectSession(item.id); if (!result) return; } $('more-menu').open = true; $('rename-current').focus(); });
    row.append(button, menu); list.append(row);
  }
}
async function selectSession(id) {
  if (running() || busy || !connected) return null;
  return perform(() => api.request('resume_session', { session_id: id }), { navigation: true });
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
      const wrapper = element('div', 'code-block'); const pre = element('pre'); const code = block.replace(/^```[^\n]*\n/, '').replace(/\n```$/, ''); pre.append(element('code', '', code)); const copy = element('button', 'code-copy', '复制代码'); copy.addEventListener('click', () => api.copyText(code).then(() => toast('已复制代码')).catch(() => showError('无法写入剪贴板'))); wrapper.append(copy, pre); container.append(wrapper); continue;
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
function positionBottomButton() {
  const box = $('messages-scroll').getBoundingClientRect(), parent = document.querySelector('.conversation').getBoundingClientRect();
  $('back-to-bottom').style.bottom = Math.max(8, parent.bottom - box.bottom + 8) + 'px';
}
function renderMessages() {
  const scroller = $('messages-scroll'), list = $('message-list');
  const changedContext = messageContext !== contextKey(state);
  if (changedContext) { list.replaceChildren(); messageNodes = []; messageContext = contextKey(state); followBottom = true; }
  const messages = state.messages || [], previous = new Map(messageNodes.map(record => [record.id, record])), nextNodes = [];
  $('welcome').hidden = messages.length > 0;
  messages.forEach((message, index) => {
    const isUser = message.role === 'user', id = message.id || `legacy-${index}-${message.role}`;
    let record = previous.get(id);
    if (!record || record.role !== message.role) {
      const article = element('article', `message ${isUser ? 'user' : 'assistant'}`), header = element('div', 'message-header');
      article.dataset.messageId = id;
      header.append(element('span', 'message-role', isUser ? '您' : message.role === 'tool' ? '工具结果' : '续想'));
      const copy = element('button', 'message-copy', '复制'); copy.setAttribute('aria-label', '复制此消息');
      const body = element('div', 'message-body'), stream = element('div', 'stream-label');
      record = { id, article, body, stream, role: message.role, content: null, message };
      copy.addEventListener('click', async () => { try { await api.copyText(String(record.message.content || '')); toast('已复制消息'); } catch { showError('无法写入剪贴板，请选择文本后复制'); } });
      header.append(copy); article.append(header, body, stream);
    }
    previous.delete(id); nextNodes.push(record);
    if (list.children[index] !== record.article) list.insertBefore(record.article, list.children[index] || null);
    record.message = message;
    const content = String(message.content || '');
    if (content !== record.content) {
      if (isUser) record.body.textContent = content;
      else { record.body.replaceChildren(); renderContent(record.body, content); }
      record.content = content;
    }
    record.stream.hidden = isUser || !running() || index !== messages.length - 1;
    record.stream.textContent = state.status === 'approval' ? '等待本次操作授权' : activeTools.size ? ([...activeTools.values()].some(item => item.flow !== 'delayed') ? '等待工具结果' : '生成继续 · 工具执行中') : '正在输出…';
  });
  for (const record of previous.values()) record.article.remove();
  messageNodes = nextNodes; positionBottomButton();
  if (followBottom) scroller.scrollTop = scroller.scrollHeight;
  $('back-to-bottom').hidden = followBottom || messages.length === 0;
}
function renderLedger() {
  const list = $('ledger-list');
  const openEntries = new Set([...list.querySelectorAll('details[open]')].map(node => node.dataset.entry));
  list.replaceChildren();
  const ledger = [...(state.ledger || [])];
  for (const item of activeTools.values()) if (!ledger.some(record => String(record.id) === String(item.id) && record.tool === item.tool)) ledger.push(item);
  $('ledger-count').textContent = number(ledger.length);
  const latest = ledger.at(-1);
  $('execution-summary').replaceChildren(document.createTextNode(latest ? `${latest.status === 'running' ? (latest.flow === 'delayed' ? '工具执行中，生成继续' : '等待工具结果') : '最近执行'} · ${latest.tool || '工具'} · ${latest.path || latest.output_summary || ''}` : running() ? '正在生成，尚无工具执行' : '尚无执行记录'), element('span', '', '查看详情 →'));
  $('execution-summary').title = latest?.output_summary || latest?.path || '打开执行账本';
  if (!ledger.length) {
    const empty = element('div', 'ledger-empty'); empty.append(element('span', 'empty-ledger-mark', '≡'), element('h3', '', '尚无工具执行'), element('p', '', '文件读写、命令和联网操作将在这里逐条记录。')); list.append(empty); return;
  }
  const statuses = { success: '成功', failed: '失败', pending: '排队', running: '执行中', approval: '等待授权', cancelled: '已停止', denied: '已拒绝', error: '错误', skipped: '已跳过' };
  const flows = { delayed: '持续执行', blocking: '等待结果', confirm: '需授权' };
  const risks = { low: '低风险', medium: '中风险', high: '高风险' };
  ledger.forEach((item, index) => {
    const entry = element('details', 'ledger-entry'); entry.dataset.entry = `${item.id}-${index}`; entry.dataset.receiptId = String(item.id || ''); entry.open = openEntries.has(entry.dataset.entry);
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
  form.elements.namedItem('thinking_mode').value = state.config.thinking_mode || 'disabled';
  form.elements.namedItem('reasoning_effort').value = state.config.reasoning_effort || 'high';
  form.elements.namedItem('api_key').value = '';
  form.elements.namedItem('api_key').disabled = false;
  form.elements.namedItem('clear_api_key').checked = false;
  $('key-state').textContent = state.settings_warning ? '未解锁 · 请替换或清除密钥' : state.config.has_api_key ? '已加密保存 · 留空保留' : '尚未保存';
  $('settings-error').hidden = true;
  updatePolicy(); $('settings-dialog').showModal();
}
function closeSettings() { if (!busy) { $('settings-dialog').close(); $('settings-form').elements.namedItem('api_key').value = ''; } }
function updatePolicy() { $('policy-description').textContent = policies[$('settings-form').elements.namedItem('security_profile').value]; }
async function chooseWorkspace() { await perform(() => api.chooseWorkspace(), { navigation: true }); }
for (const id of ['workspace', 'setup-workspace']) $(id).addEventListener('click', chooseWorkspace);
for (const id of ['settings-top', 'settings-side', 'setup-model', 'policy-button']) $(id).addEventListener('click', openSettings);
for (const id of ['close-settings', 'cancel-settings']) $(id).addEventListener('click', closeSettings);
$('settings-dialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); else $('settings-form').elements.namedItem('api_key').value = ''; });
$('settings-form').elements.namedItem('security_profile').addEventListener('change', updatePolicy);
$('settings-form').elements.namedItem('clear_api_key').addEventListener('change', event => { $('settings-form').elements.namedItem('api_key').disabled = event.target.checked; });
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault(); $('settings-error').hidden = true;
  const values = Object.fromEntries(new FormData(event.currentTarget));
  for (const field of ['max_tokens', 'max_run_turns', 'max_run_seconds']) values[field] = Number(values[field]);
  values.clear_api_key = event.currentTarget.elements.namedItem('clear_api_key').checked;
  values.thinking_mode = event.currentTarget.elements.namedItem('thinking_mode').value;
  values.reasoning_effort = event.currentTarget.elements.namedItem('reasoning_effort').value;
  const result = await perform(() => api.request('configure', values), { settings: true });
  if (result) { closeSettings(); $('error-banner').hidden = true; toast('接口与权限设置已保存'); }
});
$('new-session').addEventListener('click', () => perform(() => api.request('new_session'), { navigation: true }));
$('fork').addEventListener('click', () => perform(() => api.request('fork_session'), { notify: '已从当前会话创建分支', navigation: true }));
$('compact').addEventListener('click', () => perform(() => api.request('compact'), { notify: '上下文压缩已处理' }));
$('export').addEventListener('click', async () => { const result = await perform(() => api.exportDialog()); if (result?.saved) toast('会话已导出'); });
function toggleLedger(force) {
  if (typeof force === 'boolean') $('workbench').classList.toggle('ledger-hidden', !force);
  else $('workbench').classList.toggle('ledger-hidden');
  const visible = !$('workbench').classList.contains('ledger-hidden');
  $('toggle-ledger').setAttribute('aria-expanded', String(visible));
  $('toggle-ledger').textContent = visible ? '收起工作台' : '工作台';
  $('inspector').setAttribute('aria-hidden', String(!visible));
  if (visible) onWorkspacePanelShown(); else onWorkspacePanelHidden();
}
$('toggle-ledger').addEventListener('click', () => toggleLedger());
$('close-ledger').addEventListener('click', () => toggleLedger(false));
$('execution-summary').addEventListener('click', () => { selectWorkspaceTab('execution'); toggleLedger(true); });
$('dismiss-error').addEventListener('click', () => { $('error-banner').hidden = true; });
$('prompt').addEventListener('input', () => { resizePrompt(); saveDraft(); updateControls(); });
$('prompt').addEventListener('compositionstart', () => { composing = true; });
$('prompt').addEventListener('compositionend', () => { composing = false; });
$('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.isComposing && !composing && event.keyCode !== 229) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('composer').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || sending || pendingInput || !canRun() || !$('prompt').value.trim()) return;
  if ($('prompt').value.length > 200000) { showError('每条任务最多20万字符'); return; }
  if (queue.filter(item => item.status !== 'running').length >= 20) { showError('最多20条待发任务，请先移除或等待执行。'); return; }
  await submitPrompt($('prompt').value);
});
$('stop').addEventListener('click', () => perform(() => api.request('cancel'), { notify: '已停止当前运行，后续任务已保留并暂停' }));
for (const [id, approved] of [['approve', true], ['reject', false]]) $(id).addEventListener('click', async () => {
  const request_id = state.pending_approval?.request_id;
  if (!request_id) return;
  const result = await perform(() => api.request('approve', { request_id, approved }));
  if (result?.accepted) { state.pending_approval = null; renderApproval(); scheduleRefresh(); }
});
$('acknowledge-recovery').addEventListener('click', async () => { const result = await perform(() => api.request('acknowledge_recovery'), { notify: '已确认恢复；旧操作不会自动重放' }); if (result) $('error-banner').hidden = true; });
document.addEventListener('keydown', event => {
  if (!event.ctrlKey || event.altKey || event.isComposing || composing) return;
  const key = event.key.toLowerCase();
  if (key === 'k') { event.preventDefault(); if (!$('command-dialog').open && !document.querySelector('dialog[open]')) openCommand(); }
  else if (document.querySelector('dialog[open]')) return;
  else if (key === 'n') { event.preventDefault(); if (!$('new-session').disabled) $('new-session').click(); }
  else if (key === 'b') { event.preventDefault(); toggleSidebar(); }
  else if (key === ',') { event.preventDefault(); openSettings(); }
  else if (key === '/') { event.preventDefault(); $('shortcuts-dialog').showModal(); }
});
api.onEvent(event => {
  if (event.type === 'backend_error') { connected = false; state.status = 'error'; state.pending_approval = null; activeTools.clear(); render(); showError(`${event.message}。请重新启动续想，再查询未确认提交的回执。`); return; }
  if (event.type === 'state' && event.state) { clearTimeout(renderTimer); renderTimer = null; applyState(event.state); return; }
  if (!acceptProjection(event)) return;
  if (event.type === 'text_delta') {
    state.messages ||= [];
    let message = event.message_id ? state.messages.find(item => item.id === event.message_id) : state.messages.at(-1);
    if (!message || message.role !== 'assistant') { message = { id: event.message_id || `stream-${projectionStream}-${projectionSeq}`, role: 'assistant', content: '' }; state.messages.push(message); }
    message.content += event.text || '';
    if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; renderMessages(); }, 64);
  } else if (event.type === 'reasoning_activity') { activityPhase = 'thinking'; statusNotice = ''; $('run-status').textContent = '正在思考'; }
  else if (event.type === 'stream_started') { activityPhase = 'generating'; statusNotice = ''; $('run-status').textContent = '正在生成'; }
  else if (event.type === 'stream_finished') { activityPhase = 'waiting_feedback'; $('run-status').textContent = '等待工具结果，随后继续生成'; }
  else if (event.type === 'status_notice') { statusNotice = event.message || ''; $('run-status').textContent = statusNotice || '正在运行'; }
  else if (event.type === 'error') { showError(event.message || event.error || '本次运行发生错误，请查看执行详情。'); scheduleRefresh(); }
  else if (event.type === 'approval_required') { state.pending_approval = event; state.status = 'approval'; renderApproval(); updateControls(); scheduleRefresh(); }
  else if (event.type === 'tool_started') { activeTools.set(`${event.channel}:${event.id}`, { ...event, status: 'running' }); renderLedger(); scheduleRefresh(); }
  else if (event.type === 'tool_completed') { activeTools.delete(`${event.channel}:${event.id}`); renderLedger(); scheduleRefresh(); markWorkspaceDirty(); }
  else if (event.type === 'run_finished') { scheduleRefresh(); markWorkspaceDirty(); }
  else if (event.type.startsWith('input_') || event.type.startsWith('queue_')) scheduleRefresh();
});
function toggleSidebar() {
  const hidden = $('workbench').classList.toggle('sidebar-hidden');
  $('toggle-sidebar').setAttribute('aria-expanded', String(!hidden));
  $('toggle-sidebar').setAttribute('aria-label', hidden ? '展开侧栏' : '收起侧栏');
}
function openCommand() {
  $('command-search').value = ''; renderCommand(); $('command-dialog').showModal(); $('command-search').focus();
}
function renderCommand() {
  const query = $('command-search').value.trim().toLowerCase(), list = $('command-results'); list.replaceChildren();
  const actions = [['新建会话','new-session'],['设置','settings-top'],['选择工作区','workspace'],['最近工作区','recent-workspaces'],['查看执行账本','execution-summary'],['快捷键','show-shortcuts'],['创建分支','fork'],['压缩上下文','compact'],['导出会话','export']];
  for (const [title,id] of actions.filter(([title]) => !query || title.includes(query))) {
    const button = element('button','command-result',title); button.disabled = $(id).disabled;
    button.addEventListener('click', () => { $('command-dialog').close(); $(id).click(); }); list.append(button);
  }
  for (const session of (state.sessions || []).filter(item => !query || (item.title || '新会话').toLowerCase().includes(query))) {
    const button = element('button','command-result',session.title || '新会话'); button.append(element('small','',session.archived ? '会话 · 已归档' : session.pinned ? '会话 · 已置顶' : '会话'));
    button.disabled = running() || busy || !connected;
    button.addEventListener('click', () => { $('command-dialog').close(); selectSession(session.id); }); list.append(button);
  }
  if (!list.childElementCount) list.append(element('p','side-empty','没有匹配的会话或操作'));
}
function openRename() {
  if ($('rename-current').disabled) return;
  $('more-menu').open = false; $('rename-input').value = currentSession()?.title || '新会话';
  $('rename-dialog').showModal(); $('rename-input').select();
}
function initializeNavigation() {
  $('toggle-sidebar').addEventListener('click', toggleSidebar);
  $('session-search').addEventListener('input', () => { renderSessions(); updateControls(); });
  $('show-archived').addEventListener('click', () => { showArchived = !showArchived; $('show-archived').setAttribute('aria-pressed',String(showArchived)); $('show-archived').textContent = showArchived ? '隐藏归档' : '显示归档'; renderSessions(); updateControls(); });
  $('rename-current').addEventListener('click', openRename);
  $('cancel-rename').addEventListener('click', () => $('rename-dialog').close());
  $('rename-form').addEventListener('submit', async event => { event.preventDefault(); const title = $('rename-input').value.trim(); if (!title) return; const result = await perform(() => api.request('update_session',{session_id:state.session_id,title})); if (result) $('rename-dialog').close(); });
  $('pin-current').addEventListener('click', () => { $('more-menu').open = false; perform(() => api.request('update_session',{session_id:state.session_id,pinned:!currentSession()?.pinned})); });
  $('archive-current').addEventListener('click', () => { $('more-menu').open = false; perform(() => api.request('update_session',{session_id:state.session_id,archived:!currentSession()?.archived})); });
  $('open-command').addEventListener('click', openCommand);
  $('close-command').addEventListener('click', () => $('command-dialog').close());
  $('command-search').addEventListener('input',renderCommand);
  $('command-search').addEventListener('keydown',event => { if (event.key === 'ArrowDown') { event.preventDefault(); $('command-results').querySelector('button:not(:disabled)')?.focus(); } if (event.key === 'Enter') { event.preventDefault(); $('command-results').querySelector('button:not(:disabled)')?.click(); } });
  $('command-results').addEventListener('keydown',event => { if (!['ArrowDown','ArrowUp'].includes(event.key)) return; event.preventDefault(); const buttons = [...$('command-results').querySelectorAll('button:not(:disabled)')], index = buttons.indexOf(document.activeElement); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus(); });
  $('show-shortcuts').addEventListener('click', () => { $('more-menu').open = false; $('shortcuts-dialog').showModal(); });
  $('close-shortcuts').addEventListener('click', () => $('shortcuts-dialog').close());
  $('recent-workspaces').addEventListener('click', () => {
    const list = $('recent-list'); list.replaceChildren();
    for (const item of state.recent_workspaces || []) {
      const button = element('button','recent-item',item.name || item.path); button.append(element('small','',item.path));
      button.disabled = busy || running() || !connected;
      button.addEventListener('click', async () => { const result = await perform(() => api.openRecentWorkspace(item.path), { navigation: true }); if (result) $('workspace-dialog').close(); }); list.append(button);
    }
    if (!list.childElementCount) list.append(element('p','muted','暂无最近工作区'));
    $('workspace-dialog').showModal();
  });
  $('close-recent').addEventListener('click', () => $('workspace-dialog').close());
  $('resume-queue').addEventListener('click', () => perform(() => api.request('resume_queue')));
  $('pause-queue').addEventListener('click', () => perform(() => api.request('pause_queue')));
  $('messages-scroll').addEventListener('scroll', () => { const box = $('messages-scroll'); followBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 64; $('back-to-bottom').hidden = followBottom || !state.messages?.length; });
  $('back-to-bottom').addEventListener('click', () => { followBottom = true; $('messages-scroll').scrollTop = $('messages-scroll').scrollHeight; $('back-to-bottom').hidden = true; });
  document.addEventListener('click',event => { if (!$('more-menu').contains(event.target)) $('more-menu').open = false; });
  document.addEventListener('keydown',event => { if (event.key === 'Escape' && $('more-menu').open) { $('more-menu').open = false; $('more-menu').querySelector('summary').focus(); } });
  for (const id of ['settings-dialog','command-dialog','rename-dialog','workspace-dialog','shortcuts-dialog']) $(id).addEventListener('close', () => { if (!document.querySelector('dialog[open]')) $('prompt').focus(); });
  new ResizeObserver(() => { positionBottomButton(); resizePrompt(); }).observe($('messages-scroll'));
  addEventListener('beforeunload', saveDraft);
}
initializeNavigation();
initializeInputController();
initializeWorkspacePanel();
initializeModelTools();
updateControls();
api.request('get_state').then(applyState).catch(error => { connected = false; render(); showError(error.message); });
