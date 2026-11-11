'use strict';

function initializeDeveloperSettings() {
  const dialog = $('settings-dialog');
  const editor = $('runtime-config');
  const nativeSwitch = $('settings-form').elements.namedItem('enable_native_tools');
  const nav = dialog.querySelector('.settings-nav');
  const content = dialog.querySelector('.settings-content');
  let catalog = null, activePage = null, activeContext = '', generation = 0, previewGeneration = 0;

  function makePage(name, title, searchLabel) {
    const button = element('button', '', title);
    button.id = `settings-nav-${name}`;
    button.type = 'button';
    button.dataset.settingsPage = name;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', 'false');
    button.setAttribute('aria-controls', `settings-${name}`);
    button.tabIndex = -1;
    nav.insertBefore(button, $('settings-nav-appearance'));

    const page = element('section', 'settings-page');
    page.id = `settings-${name}`;
    page.hidden = true;
    page.setAttribute('role', 'tabpanel');
    page.setAttribute('aria-labelledby', button.id);
    const heading = element('div', 'settings-page-heading');
    heading.append(element('h2', '', title));
    const label = element('label', '', searchLabel);
    const search = element('input');
    search.type = 'search';
    search.addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
    label.append(search);
    const message = element('p', 'field-hint');
    message.setAttribute('role', 'status');
    const list = element('div', 'developer-catalog-list');
    page.append(heading, label, message, list);
    content.append(page);
    search.addEventListener('input', render);
    return { page, heading, search, message, list };
  }

  const tools = makePage('extensions', '工具', '搜索工具');
  tools.heading.append(element('p', '', '此处设置向模型开放的原生工具。流式入口仍按当前执行权限运行。'));
  const skills = makePage('skills', '技能', '搜索技能');
  skills.heading.append(element('p', '', '浏览当前工作区可用的本地技能。'));
  const skillConfig = element('div', 'form-grid');
  const enabledLabel = element('label', '', '技能发现');
  const enabled = element('select');
  enabled.dataset.runtimePath = 'interfaces.skills.enabled';
  enabled.dataset.kind = 'boolean';
  for (const [value, label] of [['', '默认'], ['true', '启用'], ['false', '关闭']]) {
    const option = element('option', '', label);
    option.value = value;
    enabled.append(option);
  }
  enabledLabel.append(enabled);
  const rootsLabel = element('label', '', '额外目录');
  const roots = element('textarea');
  roots.rows = 3;
  roots.dataset.runtimePath = 'interfaces.skills.roots';
  roots.dataset.kind = 'lines';
  rootsLabel.append(roots, element('span', 'field-hint', '每行一个目录。留空恢复默认。'));
  skillConfig.append(enabledLabel, rootsLabel);
  const discovery = element('details','skill-discovery-settings');
  discovery.append(element('summary','','查找目录与发现设置'),skillConfig);
  skills.page.insertBefore(discovery, skills.search.parentElement);
  const sourceFilter = element('select'); sourceFilter.setAttribute('aria-label','技能来源');
  const sourceCount = element('span','field-hint');
  const scopeRow = element('div','skill-scope-row'); scopeRow.append(sourceFilter,sourceCount);
  skills.page.insertBefore(scopeRow, skills.search.parentElement);
  sourceFilter.addEventListener('change',renderSkills);
  const sourceNames = {'codex-repo':'项目 · Codex','claude-repo':'项目 · Claude','codex-user':'用户 · Codex','claude-user':'用户 · Claude','thinkflow-user':'用户 · ThinkFlow','claude-command':'Claude 命令','config':'额外目录','builtin':'内置'};

  const preview = element('section', 'developer-skill-preview');
  preview.hidden = true;
  const previewTitle = element('h3');
  const previewSource = element('p', 'field-hint');
  const previewBody = element('pre');
  const closePreview = element('button','text-button','关闭预览'); closePreview.type='button'; closePreview.addEventListener('click',()=>{preview.hidden=true;});
  const previewHeader = element('div','section-title'); previewHeader.append(previewTitle,closePreview);
  preview.append(previewHeader, previewSource, previewBody);
  skills.page.insertBefore(preview, skills.list);

  function contextKey() {
    return JSON.stringify([state.cwd, state.session_id, state.config, state.runtime_config]);
  }

  function draftLists() {
    const draft = JSON.parse(editor.value);
    if (!draft || typeof draft !== 'object' || Array.isArray(draft)) throw new Error('运行配置须为 JSON 对象。');
    const list = value => typeof value === 'string' ? [value] : value ?? [];
    const allow = list(draft.native_tools), deny = list(draft.disabled_native_tools);
    if (![allow, deny].every(items => Array.isArray(items) && items.every(name => typeof name === 'string'))) {
      throw new Error('原生工具名单须为工具名或工具名列表。');
    }
    return { draft, allow, deny };
  }

  function renderTools() {
    const query = tools.search.value.trim().toLowerCase();
    const matches = catalog.tools.filter(tool => `${tool.name} ${tool.description.split('\n', 1)[0]}`.toLowerCase().includes(query));
    tools.list.replaceChildren();
    let lists;
    try { lists = draftLists(); }
    catch { tools.message.textContent = '请先修正高级配置中的 JSON 或工具名单。'; }
    if (lists) tools.message.textContent = matches.length ? '' : '没有匹配的工具。';
    for (const tool of matches) {
      const row = element('div', 'developer-catalog-row');
      row.dataset.toolName = tool.name;
      const details = element('div');
      const description = tool.description.split('\n', 1)[0];
      const excluded = lists && lists.allow.length > 0 && !lists.allow.includes(tool.name);
      const denied = lists ? lists.deny.includes(tool.name) : catalog.disabled_native_tools.includes(tool.name);
      const savedDenied = catalog.disabled_native_tools.includes(tool.name);
      let nativeStatus = tool.native_enabled ? '原生已开放' : tool.on_demand ? '原生可按需开放' : '原生未开放';
      if (excluded) nativeStatus = '原生受允许列表限定';
      else if (denied !== savedDenied) nativeStatus = denied ? '保存后禁止原生调用' : '保存后取消原生禁用';
      else if (denied) nativeStatus = '原生已禁用';
      else if (!nativeSwitch.checked) nativeStatus = '原生总开关关闭';
      else if (!catalog.enable_native_tools) nativeStatus = '原生总开关待保存';
      details.append(element('strong', '', tool.name), element('p', 'field-hint', description.length > 180 ? `${description.slice(0, 180)}…` : description),
        element('p', 'field-hint', [tool.text_protocol ? '有流式入口' : '', nativeStatus, tool.extension ? '扩展工具' : ''].filter(Boolean).join(' · ')));
      const label = element('label', 'checkbox-label', '允许原生');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', `允许原生工具 ${tool.name}`);
      checkbox.checked = !excluded && !denied;
      checkbox.disabled = !lists || !!excluded;
      checkbox.addEventListener('change', () => {
        const { draft, deny } = draftLists();
        const names = new Set(deny);
        if (checkbox.checked) names.delete(tool.name);
        else names.add(tool.name);
        if (names.size) draft.disabled_native_tools = [...names];
        else delete draft.disabled_native_tools;
        editor.value = JSON.stringify(draft, null, 2);
        editor.dispatchEvent(new Event('input', { bubbles: true }));
        const next = tools.list.querySelector(`[data-tool-name="${CSS.escape(tool.name)}"] input`);
        next.focus();
      });
      label.prepend(checkbox);
      row.append(details, label);
      tools.list.append(row);
    }
  }

  async function readSkill(skill) {
    const request = ++previewGeneration;
    const context = activeContext;
    previewTitle.textContent = skill.name;
    previewSource.textContent = `${sourceNames[skill.source] || skill.source} · ${skill.path}`;
    previewBody.textContent = '正在读取技能…';
    preview.hidden = false;
    preview.scrollIntoView({ block: 'nearest' });
    let text;
    try { text = (await api.request('read_catalog_skill', { name: skill.name })).content; }
    catch (error) { text = error.message; }
    if (request === previewGeneration && context === contextKey() && dialog.open && activePage === skills) {
      previewBody.textContent = text;
      preview.scrollIntoView({block:'start'});
    }
  }

  function renderSkills() {
    const query = skills.search.value.trim().toLowerCase();
    const source = sourceFilter.value, sources = [...new Set(catalog.skills.map(skill=>skill.source))];
    sourceFilter.replaceChildren();
    for (const value of ['',...sources]) { const option=element('option','',value ? sourceNames[value] || value : '所有来源'); option.value=value; sourceFilter.append(option); }
    sourceFilter.value = sources.includes(source) ? source : '';
    const matches = catalog.skills.filter(skill => (!sourceFilter.value || skill.source === sourceFilter.value) && `${skill.name} ${skill.description} ${sourceNames[skill.source] || skill.source} ${skill.path}`.toLowerCase().includes(query));
    sourceCount.textContent = `${matches.length} 项`;
    skills.list.replaceChildren();
    skills.message.textContent = matches.length ? '' : catalog.skills_enabled ? '没有匹配的技能。' : '当前配置已关闭本机技能发现。';
    let previousSource = '';
    for (const skill of matches) {
      if (skill.source !== previousSource) { skills.list.append(element('h3','developer-source-heading',sourceNames[skill.source] || skill.source)); previousSource=skill.source; }
      const row = element('div', 'developer-catalog-row');
      const details = element('div');
      const button = element('button', 'text-button', skill.name);
      button.type = 'button';
      button.addEventListener('click', () => readSkill(skill));
      details.append(button, element('p', 'field-hint', skill.description));
      row.append(details);
      skills.list.append(row);
    }
  }

  function render() {
    if (!catalog) return;
    if (activePage === tools) renderTools();
    else if (activePage === skills) renderSkills();
  }

  async function refresh() {
    const page = dialog.open ? [tools, skills].find(item => !item.page.hidden) : null;
    const key = contextKey();
    if (page === activePage && key === activeContext) return;
    if (!dialog.open || key !== activeContext) catalog = null;
    activePage = page;
    activeContext = key;
    const request = ++generation;
    previewGeneration++;
    preview.hidden = true;
    if (!page) return;
    if (catalog) { render(); return; }
    page.list.replaceChildren();
    if (!state.cwd) { page.message.textContent = '请先选择工作区。'; return; }
    page.message.textContent = '正在读取目录…';
    try {
      const result = await api.request('runtime_catalog');
      if (request !== generation || !dialog.open) return;
      if (key !== contextKey()) { refresh(); return; }
      catalog = result;
      render();
    } catch (error) {
      if (request !== generation || !dialog.open) return;
      if (key !== contextKey()) { refresh(); return; }
      page.message.textContent = error.message;
    }
  }

  const observer = new MutationObserver(refresh);
  observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
  for (const item of [tools, skills]) observer.observe(item.page, { attributes: true, attributeFilter: ['hidden'] });
  editor.addEventListener('input', render);
  nativeSwitch.addEventListener('change', render);
}
