'use strict';
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
function applyAppearance() {
  const choice = readLocal('theme','system');
  document.documentElement.dataset.theme = choice === 'system' ? (systemTheme.matches ? 'dark' : 'light') : choice;
  document.documentElement.dataset.reducedMotion = String(readLocal('reduce-motion',false));
  for (const button of document.querySelectorAll('[data-theme-choice]')) button.setAttribute('aria-pressed',String(button.dataset.themeChoice === choice));
  $('reduce-motion').checked = readLocal('reduce-motion',false);
  updateTerminalTheme();
}
function selectSettingsPage(name) {
  for (const button of document.querySelectorAll('[data-settings-page]')) {
    const selected = button.dataset.settingsPage === name;
    button.setAttribute('aria-selected',String(selected)); button.tabIndex = selected ? 0 : -1;
    $('settings-'+button.dataset.settingsPage).hidden = !selected;
  }
  document.querySelector('.settings-content').scrollTop = 0;
}
function syncRuntimeFields() {
  let config;
  try { config = JSON.parse($('runtime-config').value); } catch { return; }
  if (!config || typeof config !== 'object' || Array.isArray(config)) return;
  for (const field of document.querySelectorAll('[data-runtime-path]')) {
    const value = field.dataset.runtimePath.split('.').reduce((value,key) => value?.[key],config);
    field.value = field.dataset.kind === 'lines' && Array.isArray(value) ? value.join('\n') : value ?? '';
  }
}
function updateThinkingSettings() {
  const form = $('settings-form'), anthropic = form.elements.provider.value === 'anthropic';
  const deepseek = form.elements.model.value.startsWith('deepseek-') || /^https?:\/\/api\.deepseek\.com(?:[/:]|$)/i.test(form.elements.base_url.value);
  $('thinking-settings-fields').hidden = anthropic; $('thinking-budget-field').hidden = !anthropic;
  $('thinking-mode-label').textContent = deepseek ? '思考模式' : '推理方式';
  form.elements.thinking_mode.options[0].textContent = deepseek ? '关闭' : '跟随模型默认';
  form.elements.thinking_mode.options[1].textContent = deepseek ? '启用' : '指定思考强度';
  for (const option of form.elements.reasoning_effort.options) option.hidden = deepseek && !['low','high','max'].includes(option.value);
  form.elements.reasoning_effort.disabled = form.elements.thinking_mode.value !== 'enabled';
  $('thinking-settings-hint').textContent = anthropic ? '0 关闭；启用时至少 1024 token，且小于「上下文与运行」中的输出上限。' : deepseek ? '关闭、低、高、最高，按所选档位发送到 DeepSeek。' : '默认不覆盖模型设置；指定后按该接口的 reasoning_effort 参数发送。';
}
function initializeSettingsPanel() {
  function searchSettings() {
    const query = $('settings-search').value.trim().toLowerCase(), list = $('settings-search-results');
    list.replaceChildren(); list.hidden = !query;
    if (!query) return;
    const candidates = [...document.querySelectorAll('[data-settings-page]')].map(button=>({title:button.textContent,page:button.dataset.settingsPage,field:null}));
    for (const label of $('settings-form').querySelectorAll('.settings-page label')) {
      if (label.classList.contains('visually-hidden') || label.hidden) continue;
      const text = label.cloneNode(true); for (const child of text.querySelectorAll('input,select,textarea,.field-hint')) child.remove();
      const field = label.querySelector('input,select,textarea');
      if (field) candidates.push({title:text.textContent.trim(),page:label.closest('.settings-page').id.slice(9),field});
    }
    for (const item of candidates.filter(item=>`${item.title} ${item.field?.name || item.field?.dataset.runtimePath || ''}`.toLowerCase().includes(query)).slice(0,8)) {
      const button = element('button','settings-search-result',item.title); button.type='button';
      button.addEventListener('click',()=>{selectSettingsPage(item.page);list.hidden=true;$('settings-search').value='';if(item.field){const details=item.field.closest('details');if(details)details.open=true;item.field.focus();item.field.scrollIntoView({block:'center'});}else $('settings-nav-'+item.page).focus();}); list.append(button);
    }
    if (!list.childElementCount) list.append(element('p','field-hint','没有匹配的设置。'));
  }
  $('settings-search').addEventListener('input',searchSettings);
  $('settings-search').addEventListener('keydown',event=>{if(event.isComposing)return;if(event.key==='ArrowDown'){event.preventDefault();$('settings-search-results').querySelector('button')?.focus();}else if(event.key==='Escape'&&event.target.value){event.preventDefault();event.stopPropagation();event.target.value='';$('settings-search-results').hidden=true;}});
  $('settings-search-results').addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;event.preventDefault();const buttons=[...$('settings-search-results').querySelectorAll('button')],index=buttons.indexOf(document.activeElement);buttons[(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();});
  document.addEventListener('pointerdown',event=>{if(!document.querySelector('.settings-search-wrap').contains(event.target))$('settings-search-results').hidden=true;});
  for (const name of ['provider','model','base_url','thinking_mode']) $('settings-form').elements.namedItem(name).addEventListener('change',updateThinkingSettings);
  for (const button of document.querySelectorAll('[data-settings-page]')) button.addEventListener('click', () => selectSettingsPage(button.dataset.settingsPage));
  document.querySelector('.settings-nav').addEventListener('keydown', event => {
    if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
    event.preventDefault(); const buttons = [...document.querySelectorAll('[data-settings-page]')];
    const current = buttons.indexOf(document.activeElement), next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (current+(event.key === 'ArrowDown' ? 1 : -1)+buttons.length)%buttons.length;
    selectSettingsPage(buttons[next].dataset.settingsPage); buttons[next].focus();
  });
  for (const field of document.querySelectorAll('[data-runtime-path]')) field.addEventListener('change', () => {
    try {
      const config = JSON.parse($('runtime-config').value);
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('运行配置须为对象');
      const parts = field.dataset.runtimePath.split('.'), key = parts.pop();
      let target = config;
      for (const part of parts) {
        if (!(part in target)) target[part] = {};
        if (!target[part] || typeof target[part] !== 'object' || Array.isArray(target[part])) throw new Error(`${part} 须为对象`);
        target = target[part];
      }
      if (field.value === '') delete target[key];
      else target[key] = field.dataset.kind === 'boolean' ? field.value === 'true' : field.dataset.kind === 'lines' ? field.value.split('\n').map(line=>line.trim()).filter(Boolean) : Number(field.value);
      if (parts.length === 1 && Object.keys(target).length === 0) delete config[parts[0]];
      $('runtime-config').value = JSON.stringify(config,null,2);
    }
    catch (error) { selectSettingsPage('advanced'); $('runtime-config').focus(); $('settings-error').textContent = `请修正高级配置：${error.message}`; $('settings-error').hidden = false; }
  });
  $('runtime-config').addEventListener('input',syncRuntimeFields);
  for (const button of document.querySelectorAll('[data-theme-choice]')) button.addEventListener('click', () => { writeLocal('theme',button.dataset.themeChoice); applyAppearance(); });
  $('reduce-motion').addEventListener('change', () => { writeLocal('reduce-motion',$('reduce-motion').checked); applyAppearance(); });
  systemTheme.addEventListener('change',applyAppearance);
  // Invalid required controls can belong to a different settings category.
  $('settings-form').addEventListener('invalid', event => {
    const page = event.target.closest('.settings-page');
    if (page) selectSettingsPage(page.id.slice('settings-'.length));
  },true);
  applyAppearance();
}
