'use strict';
let activeComposerPopover = '', contextHideTimer, contextPinned = false, modelPickerBusy = false, closingComposerPopover = false;
const compactNumber = value => value >= 1000 ? `${(value/1000).toFixed(value >= 10000 ? 0 : 1)}k` : String(value);
const modelEndpointKey = () => `models:${state.config.provider}:${state.config.base_url}`;
function closeComposerPopover(focusPrompt = false) {
  if (activeComposerPopover) {
    const popup = $(activeComposerPopover); activeComposerPopover = '';
    closingComposerPopover = true; popup.hidePopover(); closingComposerPopover = false;
  }
  if (focusPrompt) $('prompt').focus();
}
function positionComposerPopover(id, anchorId) {
  const popup = $(id), anchor = $(anchorId).getBoundingClientRect();
  popup.style.left = `${Math.min(Math.max(12,anchor.left),innerWidth-popup.offsetWidth-12)}px`;
  popup.style.top = `${Math.max(12,anchor.top-popup.offsetHeight-8)}px`;
}
function openComposerPopover(id, anchorId, focus = false) {
  if (closingComposerPopover) return;
  clearTimeout(contextHideTimer);
  if (activeComposerPopover && activeComposerPopover !== id) closeComposerPopover();
  const popup = $(id);
  if (!popup.matches(':popover-open')) popup.showPopover();
  activeComposerPopover = id; $(anchorId).setAttribute('aria-expanded','true');
  positionComposerPopover(id,anchorId);
  if (focus) (popup.querySelector('input') || popup.querySelector('[aria-pressed=true]:not(:disabled)') || popup.querySelector('button:not(:disabled)')).focus();
}
async function saveQuickConfig(patch, close = true) {
  const {has_api_key,...config} = state.config;
  const result = await perform(() => api.request('configure',{...config,...patch}));
  if (result && close) closeComposerPopover(true);
  return result;
}
function renderComposerControls() {
  for (const button of document.querySelectorAll('[data-policy-choice]')) {
    button.setAttribute('aria-pressed',String(button.dataset.policyChoice === state.config.security_profile));
    button.disabled = busy || running() || !connected;
  }
  const context = state.context || {}, estimate = context.last_input_tokens_estimate || 0;
  $('context-compact-value').textContent = estimate ? `≈${compactNumber(estimate)}` : '';
  $('context-label').title = estimate ? `最近一次输入约 ${number(estimate)} token · 查看上下文` : '查看上下文';
  $('context-token-value').textContent = estimate ? `≈ ${number(estimate)} token` : '暂无输入';
  $('context-estimate-label').textContent = estimate ? '最近一次输入 · 本地估算' : '首次请求后显示输入估算';
  $('context-retained').textContent = `${number(context.messages)} 条 · ${number(context.chars)} 字符`;
  $('context-compactions').textContent = `${number(context.compactions)} 次`;
  const compaction = state.runtime_config?.compaction || {};
  $('context-budget').textContent = compaction.enabled === false ? '已关闭' : compaction.max_input_tokens > 0 ? `输入阈值 ${number(compaction.max_input_tokens)} token` : `${number(compaction.max_messages ?? 80)} 条 / ${number(compaction.max_chars ?? 200000)} 字符`;
  $('context-compact').disabled = $('compact').disabled;
  $('context-settings').disabled = busy || running() || !connected;
  const windowed = state.transcript_window?.total > state.transcript_window?.shown;
  $('context-window-note').hidden = !windowed;
  $('context-window-note').textContent = windowed ? `界面显示最近 ${state.transcript_window.shown} 条，完整会话保存在本机。` : '';
  $('thinking-button').disabled = busy || running() || !connected || !state.config.model;
  const thought = state.thinking;
  const labels = {default:'默认',off:'关闭',none:'无',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高'};
  const label = typeof thought.setting === 'number' ? (thought.setting ? `${compactNumber(thought.setting)}` : '关闭') : labels[thought.setting];
  $('thinking-label').textContent = label; $('thinking-button').title = `思考 · ${thought.label}`;
}
function renderThinkingPicker() {
  const thought = state.thinking, popup = $('thinking-popover'); popup.replaceChildren();
  const names = {default:'跟随模型',off:'关闭思考',none:'不推理',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高'};
  const values = thought.kind === 'anthropic'
    ? [...new Set([0,1024,4096,8192,16384,thought.setting])].filter(value=>value===0 || value < thought.output_limit)
    : thought.kind === 'deepseek' ? ['off','low','high','max'] : ['default','none','minimal','low','medium','high','xhigh','max'];
  for (const value of values) {
    const button = element('button','choice-row'); button.dataset.thinkingChoice = String(value); button.setAttribute('aria-pressed',String(value === thought.setting));
    button.append(element('span','',typeof value === 'number' ? value ? `${number(value)} token` : '关闭思考' : names[value]),element('span','choice-check','✓'));
    button.addEventListener('click',()=>saveQuickConfig(thought.kind === 'anthropic' ? {thinking_budget:value} : ['default','off'].includes(value) ? {thinking_mode:'disabled'} : {thinking_mode:'enabled',reasoning_effort:value})); popup.append(button);
  }
  if (thought.kind === 'anthropic') {
    const custom = element('button','popover-link','自定义预算…'); custom.addEventListener('click',()=>{closeComposerPopover();openSettings();$('settings-form').elements.namedItem('thinking_budget').focus();}); popup.append(custom);
  }
}
function renderModelPicker() {
  const query = $('model-picker-search').value.trim(), current = state.config.model;
  const names = [...new Set([current,...readLocal(modelEndpointKey(),[])].filter(Boolean))];
  const list = $('model-picker-list'); list.replaceChildren();
  for (const name of names.filter(name => name.toLowerCase().includes(query.toLowerCase()))) {
    const button = element('button','choice-row'); button.title = name; button.setAttribute('aria-pressed',String(name === current));
    button.append(element('span','model-choice-name',name),element('span','choice-check','✓'));
    button.addEventListener('click', () => saveQuickConfig({model:name})); list.append(button);
  }
  $('model-picker-custom').hidden = !query || names.includes(query);
  $('model-picker-custom').textContent = `使用「${query}」`;
  $('refresh-model-picker').disabled = modelPickerBusy;
}
function rememberCatalog(result) {
  const names = result.models.map(item => typeof item === 'string' ? item : item.id || item.name);
  writeLocal(modelEndpointKey(),names);
}
function initializeComposerControls() {
  const pairs = [['policy-popover','policy-button'],['model-popover','settings-top'],['context-popover','context-label'],['thinking-popover','thinking-button']];
  for (const [id,anchor] of pairs) {
    $(id).addEventListener('beforetoggle',event => { if (event.newState === 'closed') { closingComposerPopover = true; queueMicrotask(()=>{closingComposerPopover = false;}); } });
    $(id).addEventListener('toggle',event => { if (event.newState === 'closed') { $(anchor).setAttribute('aria-expanded','false'); if (activeComposerPopover === id) activeComposerPopover = ''; if (id === 'context-popover') contextPinned = false; } });
    $(id).addEventListener('keydown',event => {
      if (event.isComposing) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeComposerPopover(true); return; }
      if (!['ArrowUp','ArrowDown'].includes(event.key) || event.target.tagName === 'SELECT') return;
      const buttons = [...$(id).querySelectorAll('button:not(:disabled):not([hidden])')].filter(button => button.getClientRects().length);
      event.preventDefault(); const index = buttons.indexOf(document.activeElement); buttons[(index+(event.key === 'ArrowDown' ? 1 : -1)+buttons.length)%buttons.length]?.focus();
    });
  }
  $('policy-button').addEventListener('click', () => { renderComposerControls(); openComposerPopover('policy-popover','policy-button',true); });
  for (const button of document.querySelectorAll('[data-policy-choice]')) button.addEventListener('click', () => saveQuickConfig({security_profile:button.dataset.policyChoice}));
  $('permission-settings').addEventListener('click', () => { closeComposerPopover(); openSettings('tools'); });
  $('settings-top').addEventListener('click', () => {
    if (!state.config.base_url) { openSettings(); return; }
    $('model-picker-search').value = ''; $('model-picker-feedback').textContent = ''; renderModelPicker(); openComposerPopover('model-popover','settings-top',true);
  });
  $('model-connection-settings').addEventListener('click', () => { closeComposerPopover(); openSettings(); });
  $('model-picker-search').addEventListener('input', () => { renderModelPicker(); positionComposerPopover('model-popover','settings-top'); });
  $('model-picker-search').addEventListener('keydown',event => { if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); const value = event.target.value.trim(); if (value) saveQuickConfig({model:value}); } });
  $('model-picker-custom').addEventListener('click', () => saveQuickConfig({model:$('model-picker-search').value.trim()}));
  $('refresh-model-picker').addEventListener('click', async () => {
    if (modelPickerBusy) return;
    modelPickerBusy = true; $('refresh-model-picker').disabled = true; $('model-picker-feedback').textContent = '正在获取模型列表…';
    try { const result = await api.request('model_catalog'); rememberCatalog(result); $('model-picker-feedback').textContent = result.models.length ? '' : '端点未返回模型，可直接输入名称。'; renderModelPicker(); }
    catch (error) { $('model-picker-feedback').textContent = error.message; }
    finally { modelPickerBusy = false; $('refresh-model-picker').disabled = false; positionComposerPopover('model-popover','settings-top'); }
  });
  $('thinking-button').addEventListener('click',()=>{renderThinkingPicker();openComposerPopover('thinking-popover','thinking-button',true);});
  const contextOpen = () => { renderComposerControls(); openComposerPopover('context-popover','context-label'); };
  const contextLeave = () => { if (!contextPinned) contextHideTimer = setTimeout(()=>{if(activeComposerPopover === 'context-popover') closeComposerPopover();},200); };
  $('context-label').addEventListener('mouseenter',contextOpen); $('context-label').addEventListener('focus',contextOpen); $('context-label').addEventListener('mouseleave',contextLeave);
  $('context-label').addEventListener('click', event => { contextPinned = true; contextOpen(); if (event.detail === 0) openComposerPopover('context-popover','context-label',true); });
  $('context-label').addEventListener('keydown', event => { if (event.key === 'ArrowDown') { event.preventDefault(); contextPinned = true; openComposerPopover('context-popover','context-label',true); } });
  $('context-popover').addEventListener('mouseenter',()=>clearTimeout(contextHideTimer)); $('context-popover').addEventListener('mouseleave',contextLeave);
  $('context-label').addEventListener('blur',event=>{if(!$('context-popover').contains(event.relatedTarget)) contextLeave();});
  $('context-compact').addEventListener('click', () => { closeComposerPopover(); $('compact').click(); });
  $('context-settings').addEventListener('click', () => { closeComposerPopover(); openSettings('context'); });
  $('expand-approval-command').addEventListener('click', () => { const expanded = $('approval-command').classList.toggle('expanded'); $('expand-approval-command').setAttribute('aria-expanded',String(expanded)); $('expand-approval-command').textContent = expanded ? '收起命令' : '展开命令'; });
  addEventListener('resize',()=>closeComposerPopover());
}
