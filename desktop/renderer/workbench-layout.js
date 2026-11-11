'use strict';
let layoutReady = false;
let preferredSidebarWidth = 240, preferredInspectorWidth = 368;
const paneTimers = new Map();
let composerMove;
function animateComposerPosition(before) {
  composerMove?.cancel();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.reducedMotion === 'true') return;
  const after = $('composer').getBoundingClientRect();
  composerMove = $('composer').animate([{transform:`translateY(${before.top-after.top}px)`},{transform:'translateY(0)'}],{duration:220,easing:'cubic-bezier(.23,1,.32,1)'});
}
function constrainPaneWidths(sidebarVisible = !$('workbench').classList.contains('sidebar-hidden')) {
  const bench = $('workbench'), inspectorVisible = !bench.classList.contains('ledger-hidden');
  const sidebar = Math.min(preferredSidebarWidth, inspectorVisible ? bench.clientWidth-680 : 320);
  bench.style.setProperty('--sidebar-width', `${Math.max(200,sidebar)}px`);
  const available = bench.clientWidth - (sidebarVisible ? Math.max(200,sidebar) : 0);
  const desired = bench.classList.contains('inspector-expanded') ? 560 : preferredInspectorWidth;
  bench.style.setProperty('--inspector-width', `${Math.max(280,Math.min(desired,available-400))}px`);
}
function animatePane(id, visible, targetWidth) {
  const pane = $(id), content = pane.querySelector('.pane-content');
  clearTimeout(paneTimers.get(id));
  content.style.width = `${visible ? targetWidth : pane.getBoundingClientRect().width}px`;
  pane.inert = !visible;
  pane.setAttribute('aria-hidden', String(!visible));
  paneTimers.set(id, setTimeout(() => { content.style.width = ''; }, 240));
}
function syncPanelButtons() {
  const visible = !$('workbench').classList.contains('ledger-hidden');
  $('open-files').setAttribute('aria-pressed', String(visible && workspaceTab === 'files'));
  $('open-changes').setAttribute('aria-pressed', String(visible && workspaceTab === 'changes'));
  $('toggle-ledger').setAttribute('aria-expanded', String(visible && ['execution','tasks'].includes(workspaceTab)));
}
function openWorkspacePane(tab) {
  if (workspaceTab === tab && !$('workbench').classList.contains('ledger-hidden')) toggleLedger(false);
  else { selectWorkspaceTab(tab); toggleLedger(true); }
}
function setSidebarVisible(visible) {
  constrainPaneWidths(visible);
  if (layoutReady) animatePane('sidebar', visible, parseFloat(getComputedStyle($('workbench')).getPropertyValue('--sidebar-width')));
  $('workbench').classList.toggle('sidebar-hidden', !visible);
  $('toggle-sidebar').setAttribute('aria-expanded', String(visible));
  $('toggle-sidebar').setAttribute('aria-label', visible ? '收起侧栏' : '展开侧栏');
  if (!visible && $('sidebar').contains(document.activeElement)) $('toggle-sidebar').focus();
}
function bindPaneResize(id, axis, initial, apply, commit) {
  const handle = $(id);
  let start, size, dragging = false;
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault(); dragging = true; start = axis === 'x' ? event.clientX : event.clientY; size = initial();
    handle.setPointerCapture(event.pointerId); $('workbench').classList.add('is-resizing');
    for (const pane of document.querySelectorAll('.pane-content')) pane.style.width = '';
  });
  handle.addEventListener('pointermove', event => { if (dragging) apply(size + (axis === 'x' ? event.clientX : event.clientY) - start); });
  const finish = () => { if (!dragging) return; dragging = false; $('workbench').classList.remove('is-resizing'); commit(); };
  handle.addEventListener('pointerup', finish); handle.addEventListener('lostpointercapture', finish);
  handle.addEventListener('keydown', event => {
    const keys = axis === 'x' ? ['ArrowLeft','ArrowRight'] : ['ArrowUp','ArrowDown'];
    if (!keys.includes(event.key)) return;
    event.preventDefault(); apply(initial() + (event.key === keys[0] ? -16 : 16)); commit();
  });
}
function initializeWorkbenchLayout() {
  const bench = $('workbench'), center = $('workspace-center');
  for (const [id, cls] of [['sidebar','sidebar-content'],['inspector','inspector-content']]) {
    const pane = $(id), content = element('div', `pane-content ${cls}`);
    for (const node of [...pane.children]) if (!node.classList.contains('resize-handle')) content.append(node);
    pane.prepend(content);
  }
  preferredSidebarWidth = readLocal('sidebar-width',240);
  preferredInspectorWidth = readLocal('inspector-width',368);
  constrainPaneWidths();
  center.style.setProperty('--terminal-height', `${readLocal('terminal-height',248)}px`);
  const clamp = (value,min,max) => Math.min(Math.max(value,min),max);
  bindPaneResize('sidebar-resize','x', () => $('sidebar').getBoundingClientRect().width,
    width => { preferredSidebarWidth = clamp(width,200,320); constrainPaneWidths(); },
    () => writeLocal('sidebar-width',preferredSidebarWidth));
  bindPaneResize('inspector-resize','x', () => -$('inspector').getBoundingClientRect().width,
    negativeWidth => { bench.classList.remove('inspector-expanded'); preferredInspectorWidth = clamp(-negativeWidth,280,center.clientWidth-400); constrainPaneWidths(); $('expand-inspector').setAttribute('aria-pressed','false'); $('expand-inspector').textContent = '展开阅读'; },
    () => writeLocal('inspector-width',preferredInspectorWidth));
  bindPaneResize('terminal-resize','y', () => -$('terminal-panel').getBoundingClientRect().height,
    negativeHeight => center.style.setProperty('--terminal-height', `${clamp(-negativeHeight,140,center.clientHeight*.5)}px`),
    () => { writeLocal('terminal-height', $('terminal-panel').getBoundingClientRect().height); fitActiveTerminal(); });
  $('open-files').addEventListener('click', () => openWorkspacePane('files'));
  $('open-changes').addEventListener('click', () => openWorkspacePane('changes'));
  document.addEventListener('keydown', event => {
    if (event.isComposing || document.querySelector('dialog[open]')) return;
    if (event.ctrlKey && event.key === '`') { event.preventDefault(); toggleTerminal(); }
    if (event.ctrlKey && event.shiftKey && ['e','g'].includes(event.key.toLowerCase())) { event.preventDefault(); openWorkspacePane(event.key.toLowerCase() === 'e' ? 'files' : 'changes'); }
  });
  new ResizeObserver(() => {
    constrainPaneWidths();
    const height = parseFloat(center.style.getPropertyValue('--terminal-height'));
    if (height > center.clientHeight*.5) center.style.setProperty('--terminal-height', `${Math.max(140,center.clientHeight*.5)}px`);
  }).observe(center);
  layoutReady = true;
  setSidebarVisible(!readLocal('sidebar-hidden',false));
}
