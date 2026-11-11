'use strict';
const terminalViews = new Map(), pendingTerminalEvents = new Map();
let activeTerminalId = '', terminalWorkspace = '', terminalGeneration = 0, terminalCreating = false, terminalFitTimer;
function terminalTheme() {
  const css = getComputedStyle(document.documentElement);
  return {background:css.getPropertyValue('--surface').trim(),foreground:css.getPropertyValue('--ink').trim(),cursor:css.getPropertyValue('--ink').trim(),selectionBackground:css.getPropertyValue('--selected').trim()};
}
function updateTerminalTheme() { for (const view of terminalViews.values()) view.term.options.theme = terminalTheme(); }
function resetTerminalWorkspace(cwd) {
  if (cwd === terminalWorkspace) return;
  terminalWorkspace = cwd; terminalGeneration++;
  for (const view of terminalViews.values()) view.term.dispose();
  terminalViews.clear(); pendingTerminalEvents.clear(); activeTerminalId = ''; terminalCreating = false;
  $('terminal-views').replaceChildren(); renderTerminalTabs();
  if (!$('workspace-center').classList.contains('terminal-hidden')) loadTerminals();
}
function renderTerminalTabs() {
  const tabs = $('terminal-tabs'); tabs.replaceChildren();
  for (const [id,view] of terminalViews) {
    const entry = element('div',`terminal-tab${id === activeTerminalId ? ' active' : ''}`);
    const select = element('button','',`${view.title}${view.exited ? ` · 已退出 ${view.exit_code}` : ''}`);
    select.setAttribute('role','tab'); select.setAttribute('aria-selected',String(id === activeTerminalId)); select.tabIndex = id === activeTerminalId ? 0 : -1;
    select.addEventListener('click', () => selectTerminal(id));
    const close = element('button','','×'); close.setAttribute('aria-label',`关闭 ${view.title}`); close.addEventListener('click', () => closeTerminal(id));
    entry.append(select,close); tabs.append(entry);
  }
  $('new-terminal').disabled = !connected || !state.cwd || terminalCreating;
}
function mountTerminal(snapshot) {
  const host = element('div','terminal-view'); host.hidden = true; host.dataset.terminalId = snapshot.id;
  $('terminal-views').append(host);
  const term = new Terminal({fontFamily:'Consolas, "Microsoft YaHei", monospace',fontSize:13,lineHeight:1.2,cursorBlink:true,scrollback:4000,theme:terminalTheme(),allowProposedApi:false});
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(host);
  const view = {term,fit,host,offset:snapshot.offset,title:snapshot.title,exited:snapshot.exited,exit_code:snapshot.exit_code,cols:snapshot.cols,rows:snapshot.rows};
  terminalViews.set(snapshot.id,view); term.write(snapshot.scrollback);
  term.onData(data => { if (!view.exited) api.request('terminal_input',{id:snapshot.id,data}).catch(error => { $('terminal-feedback').textContent = error.message; }); });
  term.attachCustomKeyEventHandler(event => !(event.ctrlKey && event.key === '`'));
  for (const event of pendingTerminalEvents.get(snapshot.id) || []) handleTerminalEvent(event);
  pendingTerminalEvents.delete(snapshot.id);
  return view;
}
function selectTerminal(id, focus = true) {
  activeTerminalId = id;
  for (const [key,view] of terminalViews) view.host.hidden = key !== id;
  renderTerminalTabs(); fitActiveTerminal();
  if (focus) terminalViews.get(id)?.term.focus();
}
function fitActiveTerminal() {
  clearTimeout(terminalFitTimer);
  const view = terminalViews.get(activeTerminalId);
  if (!view || $('workspace-center').classList.contains('terminal-hidden')) return;
  view.fit.fit();
  if (view.exited || (view.cols === view.term.cols && view.rows === view.term.rows)) return;
  view.cols = view.term.cols; view.rows = view.term.rows;
  api.request('terminal_resize',{id:activeTerminalId,cols:view.cols,rows:view.rows}).catch(error => { $('terminal-feedback').textContent = error.message; });
}
async function loadTerminals() {
  if (!connected || !state.cwd) { $('terminal-feedback').textContent = '选择工作区后即可打开终端。'; return; }
  const generation = terminalGeneration;
  try {
    const result = await api.request('terminal_list');
    if (generation !== terminalGeneration) return;
    for (const snapshot of result.terminals) if (!terminalViews.has(snapshot.id)) mountTerminal(snapshot);
    if (!terminalViews.size) await createTerminal();
    else selectTerminal(activeTerminalId || terminalViews.keys().next().value);
  } catch (error) { if (generation === terminalGeneration) $('terminal-feedback').textContent = error.message; }
}
async function createTerminal() {
  if (terminalCreating || !connected || !state.cwd) return;
  terminalCreating = true; renderTerminalTabs(); const generation = terminalGeneration;
  try {
    const snapshot = await api.request('terminal_create',{cols:100,rows:24});
    if (generation !== terminalGeneration) return;
    mountTerminal(snapshot); selectTerminal(snapshot.id); $('terminal-feedback').textContent = '';
  } catch (error) { if (generation === terminalGeneration) $('terminal-feedback').textContent = error.message; }
  finally { if (generation === terminalGeneration) { terminalCreating = false; renderTerminalTabs(); } }
}
async function closeTerminal(id) {
  try {
    await api.request('terminal_close',{id});
    const view = terminalViews.get(id); if (!view) return;
    view.term.dispose(); view.host.remove(); terminalViews.delete(id); pendingTerminalEvents.delete(id);
    if (activeTerminalId === id) activeTerminalId = terminalViews.keys().next().value || '';
    if (activeTerminalId) selectTerminal(activeTerminalId); else { renderTerminalTabs(); $('terminal-feedback').textContent = '终端已关闭。点击 ＋ 新建终端。'; }
  } catch (error) { $('terminal-feedback').textContent = error.message; }
}
function handleTerminalEvent(event) {
  const view = terminalViews.get(event.id);
  if (!view) { if (event.type === 'terminal_output') { const queued = pendingTerminalEvents.get(event.id) || []; queued.push(event); pendingTerminalEvents.set(event.id,queued); } return; }
  if (event.type === 'terminal_output') {
    if (event.offset <= view.offset) return;
    const overlap = Math.max(0,view.offset-(event.offset-event.data.length));
    view.term.write(event.data.slice(overlap)); view.offset = event.offset;
  } else if (event.type === 'terminal_exit') { view.exited = true; view.exit_code = event.exit_code; renderTerminalTabs(); }
}
function toggleTerminal(force) {
  const center = $('workspace-center'), visible = typeof force === 'boolean' ? force : center.classList.contains('terminal-hidden');
  center.classList.toggle('terminal-hidden',!visible); $('terminal-panel').inert = !visible; $('terminal-panel').setAttribute('aria-hidden',String(!visible)); $('toggle-terminal').setAttribute('aria-expanded',String(visible));
  if (visible) loadTerminals(); else if ($('terminal-panel').contains(document.activeElement)) $('toggle-terminal').focus();
}
function initializeTerminalPanel() {
  $('toggle-terminal').addEventListener('click', () => toggleTerminal()); $('close-terminal-panel').addEventListener('click', () => toggleTerminal(false)); $('new-terminal').addEventListener('click',createTerminal);
  $('terminal-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
    event.preventDefault(); const ids = [...terminalViews.keys()]; selectTerminal(ids[(ids.indexOf(activeTerminalId)+(event.key === 'ArrowRight' ? 1 : -1)+ids.length)%ids.length]);
  });
  new ResizeObserver(() => { clearTimeout(terminalFitTimer); terminalFitTimer = setTimeout(fitActiveTerminal,150); }).observe($('terminal-views'));
}
