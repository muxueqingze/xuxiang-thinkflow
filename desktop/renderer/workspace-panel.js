
'use strict';
let workspaceTab = 'execution', fileDirectory = '', previewFile = null, previewChange = null, workspaceRequestEpoch = 0;
let fileRequest = 0, changeRequest = 0, fileLoading = false, changeLoading = false, workspaceDirty = true, revertBusy = false;
let narrowSidebarWasVisible = false;
const changeReasonNames = {
  directory_metadata_only: '目录仅记录元数据，无法显示文本差异或回退',
  sensitive_path: '路径可能包含敏感资料，已阻止预览和回退',
  sensitive_content: '内容可能含凭据，已阻止预览和回退',
  snapshot_too_large: '文件超过快照上限，无法预览或回退',
  binary_file: '二进制文件暂不支持文本差异与回退',
  unconfirmed_change: '变更最终状态未确认，请核对实际文件',
  new_file_requires_manual_recovery: '新建文件可查看差异；删除须手动核对处理',
  touch_timestamp_not_reversible: '时间戳更新不能恢复原时间',
};
function changeReason(value) { return changeReasonNames[value] || (value?.startsWith('revision_conflict') ? '文件版本已变化，回退被拒绝。请刷新并核对当前文件。' : value || ''); }
function normalizeChange(value) { return { ...value, revertible: value.can_revert ?? value.revertible ?? false, after_revision: value.expected_revision ?? value.after_revision ?? value.after_hash, before_revision: value.before_revision ?? value.before_hash }; }
function resetWorkspacePanel() {
  workspaceRequestEpoch++; fileDirectory = ''; previewFile = null; previewChange = null; workspaceDirty = true;
  $('files-list').replaceChildren(); $('changes-list').replaceChildren(); $('files-list').hidden = $('changes-list').hidden = $('file-search-form').hidden = false; $('file-preview').hidden = $('change-preview').hidden = true;
  $('file-search').value = ''; $('files-feedback').textContent = ''; $('changes-feedback').textContent = '';
  if (!$('workbench').classList.contains('ledger-hidden')) setTimeout(onWorkspacePanelShown, 0);
}
function updateWorkspaceControls() {
  for (const id of ['refresh-files','file-search','files-up']) $(id).disabled = !connected || !state.cwd || fileLoading;
  $('refresh-changes').disabled = !connected || !state.cwd || changeLoading;
  $('files-up').disabled ||= !fileDirectory;
  $('attach-file').disabled = !state.cwd || !connected || sending;
  $('attach-preview-file').disabled = !previewFile || sending;
  $('open-revert').disabled = !previewChange?.revertible || running() || busy || !connected;
  $('confirm-revert').disabled = running() || revertBusy || busy || !connected;
  $('close-revert').disabled = $('cancel-revert').disabled = revertBusy;
}
function onWorkspacePanelShown() {
  if (innerWidth < 1150 && !$('workbench').classList.contains('sidebar-hidden')) {
    narrowSidebarWasVisible = true; $('workbench').classList.add('sidebar-hidden');
    $('toggle-sidebar').setAttribute('aria-expanded','false'); $('toggle-sidebar').setAttribute('aria-label','展开侧栏');
  }
  if (workspaceTab === 'files' && (workspaceDirty || !$('files-list').childElementCount) && !previewFile) loadWorkspaceFiles();
  if (workspaceTab === 'changes' && workspaceDirty && !previewChange) loadWorkspaceChanges();
}
function onWorkspacePanelHidden() {
  if (narrowSidebarWasVisible) { $('workbench').classList.remove('sidebar-hidden'); narrowSidebarWasVisible = false; $('toggle-sidebar').setAttribute('aria-expanded','true'); $('toggle-sidebar').setAttribute('aria-label','收起侧栏'); }
}
function selectWorkspaceTab(tab) {
  workspaceTab = tab;
  for (const name of ['execution','tasks','files','changes']) {
    $('tab-' + name).setAttribute('aria-selected',String(name === tab)); $('tab-' + name).tabIndex = name === tab ? 0 : -1;
    $('panel-' + name).hidden = name !== tab;
  }
  if (!$('workbench').classList.contains('ledger-hidden')) onWorkspacePanelShown();
}
function markWorkspaceDirty() {
  workspaceDirty = true;
  if (!$('workbench').classList.contains('ledger-hidden') && workspaceTab === 'changes' && !previewChange) loadWorkspaceChanges();
}
async function loadWorkspaceFiles() {
  if (!connected || !state.cwd) { $('files-feedback').textContent = '请先选择工作区。'; return; }
  const epoch = workspaceRequestEpoch, request = ++fileRequest, cwd = state.cwd;
  fileLoading = true; $('files-feedback').textContent = '正在读取文件列表…'; updateWorkspaceControls();
  try {
    const result = await api.request('workspace_files', { path: fileDirectory, query: $('file-search').value.trim() });
    if (epoch !== workspaceRequestEpoch || request !== fileRequest || cwd !== state.cwd) return;
    fileDirectory = result.path === '.' ? '' : result.path || ''; $('files-path').textContent = fileDirectory || '工作区'; $('files-path').title = fileDirectory || state.cwd;
    const list = $('files-list'); list.replaceChildren();
    for (const item of result.entries || []) {
      const row = element('button','file-entry'); row.dataset.path = item.path; row.dataset.kind = item.kind;
      row.append(element('span','file-entry-kind',item.kind === 'directory' ? '目录' : '文件'),element('strong','',item.name || item.path));
      row.append(element('small','',item.kind === 'directory' ? '打开 →' : `${number(item.size)} 字节`));
      row.addEventListener('click', () => { if (item.kind === 'directory') { fileDirectory = item.path; $('file-search').value = ''; loadWorkspaceFiles(); } else openWorkspaceFile(item.path); }); list.append(row);
    }
    $('files-feedback').textContent = result.truncated ? '当前目录条目较多，列表已截断；使用关键词筛选。' : list.childElementCount ? `${list.childElementCount} 项 · 只读浏览` : '当前目录没有匹配的文件。';
  } catch (error) { if (epoch === workspaceRequestEpoch && request === fileRequest) $('files-feedback').textContent = `${error.message}。可刷新或返回上一级。`; }
  finally { if (request === fileRequest) { fileLoading = false; updateWorkspaceControls(); } }
}
async function openWorkspaceFile(path) {
  const epoch = workspaceRequestEpoch, request = ++fileRequest, cwd = state.cwd;
  fileLoading = true; $('files-feedback').textContent = '正在读取只读预览…'; updateWorkspaceControls();
  try {
    const result = await api.request('read_workspace_file', { path });
    if (epoch !== workspaceRequestEpoch || request !== fileRequest || cwd !== state.cwd) return;
    previewFile = result; $('files-list').hidden = true; $('file-search-form').hidden = true; $('file-preview').hidden = false;
    $('preview-file-name').textContent = result.path; $('preview-file-content').textContent = result.content || '';
    $('preview-file-info').textContent = `${number(result.bytes)} 字节 · 只读${result.truncated ? ' · 预览已截断' : ''} · 版本 ${String(result.revision).slice(0,12)}`;
    $('files-feedback').textContent = '';
  } catch (error) { if (epoch === workspaceRequestEpoch && request === fileRequest) $('files-feedback').textContent = `无法预览：${error.message}。可选择其他文件或刷新。`; }
  finally { if (request === fileRequest) { fileLoading = false; updateWorkspaceControls(); } }
}
async function loadWorkspaceChanges() {
  if (!connected || !state.cwd) { $('changes-feedback').textContent = '请先选择工作区。'; return; }
  const epoch = workspaceRequestEpoch, request = ++changeRequest, key = contextKey(state);
  changeLoading = true; $('changes-feedback').textContent = '正在读取文件变更…'; updateWorkspaceControls();
  try {
    const result = await api.request('workspace_changes');
    if (epoch !== workspaceRequestEpoch || request !== changeRequest || key !== contextKey(state)) return;
    renderWorkspaceChanges(result.changes || []); workspaceDirty = false;
  } catch (error) { if (epoch === workspaceRequestEpoch && request === changeRequest) $('changes-feedback').textContent = `${error.message}。可再次刷新。`; }
  finally { if (request === changeRequest) { changeLoading = false; updateWorkspaceControls(); } }
}
function renderWorkspaceChanges(changes) {
  const list = $('changes-list'); list.replaceChildren(); $('changes-count').textContent = changes.length;
  const names = { write: '写入', edit: '修改', create: '新建', delete: '删除', reverted: '已回退', success: '成功', conflict: '版本冲突', unavailable: '无法回退' };
  for (const raw of changes.slice().reverse()) {
    const item = normalizeChange(raw);
    const entry = element('button','change-entry'); entry.dataset.changeId = item.id;
    entry.append(element('strong','',item.path),element('small','',`${names[item.operation] || item.operation || '变更'} · ${names[item.status] || item.status || '已记录'}${item.revertible ? ' · 可预览回退' : ''}${item.unavailable_reason ? ' · ' + changeReason(item.unavailable_reason) : ''}`));
    entry.disabled = item.can_diff === false;
    entry.addEventListener('click', () => openWorkspaceChange(item.id)); list.append(entry);
  }
  $('changes-feedback').textContent = changes.length ? `${changes.length} 条记录 · 回退前会核对当前文件版本` : '本会话尚无文件变更。命令产生的变更不一定可单独回退。';
}
async function openWorkspaceChange(id) {
  const epoch = workspaceRequestEpoch, request = ++changeRequest, key = contextKey(state);
  changeLoading = true; $('changes-feedback').textContent = '正在读取变更差异…'; updateWorkspaceControls();
  try {
    const result = await api.request('workspace_diff', { change_id: id });
    if (epoch !== workspaceRequestEpoch || request !== changeRequest || key !== contextKey(state)) return;
    previewChange = { ...normalizeChange(result), id }; $('changes-list').hidden = true; $('change-preview').hidden = false;
    $('change-file-name').textContent = result.path; $('change-diff').textContent = result.diff || '这次变更没有可显示的文本差异。';
    $('change-file-info').textContent = `${previewChange.revertible ? '可回退' : '当前不能回退'}${result.unavailable_reason ? ' · ' + changeReason(result.unavailable_reason) : ''} · 版本 ${String(previewChange.after_revision || '').slice(0,12)}`;
    $('changes-feedback').textContent = '';
  } catch (error) { if (epoch === workspaceRequestEpoch && request === changeRequest) $('changes-feedback').textContent = `${error.message}。请刷新变更列表。`; }
  finally { if (request === changeRequest) { changeLoading = false; updateWorkspaceControls(); } }
}
function renderTaskPlan() {
  const plan = state.task_plan, list = $('task-plan-list'); list.replaceChildren();
  $('task-plan-explanation').textContent = plan?.explanation || (plan?.steps?.length ? '本次任务计划' : '还没有任务计划。多步骤任务可要求续想先制定计划并更新进度。');
  const names = { pending: '待处理', in_progress: '正在进行', completed: '已完成（模型标记）', blocked: '遇到阻碍' };
  for (const step of plan?.steps || []) {
    const entry = element('section',`task-plan-entry ${step.status}`); entry.dataset.stepId = step.id;
    entry.append(element('span','task-plan-status',names[step.status] || '待处理'),element('h3','',step.title),element('p','task-acceptance',`验收条件：${step.acceptance || '未填写'}`));
    if (step.evidence?.length) {
      const evidence = element('div','task-evidence'); evidence.append(element('span','','工具回执：'));
      for (const id of step.evidence) { const button = element('button','text-button',String(id)); button.addEventListener('click', () => { selectWorkspaceTab('execution'); toggleLedger(true); const target = [...$('ledger-list').querySelectorAll('.ledger-entry')].find(node => node.dataset.receiptId === String(id)); if (target) { target.open = true; $('ledger-list').scrollTop = target.offsetTop - $('ledger-list').offsetTop; target.querySelector('summary').focus(); } else toast('该回执不在当前账本窗口，可导出会话核对完整记录'); }); evidence.append(button); }
      entry.append(evidence);
    }
    list.append(entry);
  }
}
function initializeWorkspacePanel() {
  for (const name of ['execution','tasks','files','changes']) $('tab-' + name).addEventListener('click', () => selectWorkspaceTab(name));
  document.querySelector('.inspector-tabs').addEventListener('keydown', event => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); const names=['execution','tasks','files','changes']; let index=names.indexOf(workspaceTab); index=event.key==='Home'?0:event.key==='End'?3:(index+(event.key==='ArrowRight'?1:-1)+4)%4; selectWorkspaceTab(names[index]); $('tab-'+names[index]).focus(); });
  $('expand-inspector').addEventListener('click', () => { const expanded = $('workbench').classList.toggle('inspector-expanded'); $('expand-inspector').setAttribute('aria-pressed',String(expanded)); $('expand-inspector').textContent = expanded ? '收窄阅读' : '展开阅读'; });
  $('attach-file').addEventListener('click', () => { selectWorkspaceTab('files'); toggleLedger(true); });
  $('refresh-files').addEventListener('click', () => { if (previewFile) openWorkspaceFile(previewFile.path); else loadWorkspaceFiles(); });
  $('files-up').addEventListener('click', () => { previewFile = null; $('file-preview').hidden = true; $('files-list').hidden = $('file-search-form').hidden = false; fileDirectory = fileDirectory.split(/[\\/]/).filter(Boolean).slice(0,-1).join('/'); $('file-search').value = ''; loadWorkspaceFiles(); });
  $('file-search-form').addEventListener('submit', event => { event.preventDefault(); loadWorkspaceFiles(); });
  $('close-file-preview').addEventListener('click', () => { previewFile = null; $('file-preview').hidden = true; $('files-list').hidden = $('file-search-form').hidden = false; loadWorkspaceFiles(); });
  $('attach-preview-file').addEventListener('click', () => attachWorkspaceFile(previewFile));
  $('refresh-changes').addEventListener('click', () => { if (previewChange) openWorkspaceChange(previewChange.id); else loadWorkspaceChanges(); });
  $('close-change-preview').addEventListener('click', () => { previewChange = null; $('change-preview').hidden = true; $('changes-list').hidden = false; loadWorkspaceChanges(); });
  $('open-revert').addEventListener('click', () => {
    if (!previewChange?.revertible || running() || busy) return;
    $('revert-path').textContent = previewChange.path; $('revert-diff').textContent = previewChange.diff || ''; $('revert-error').hidden = true;
    $('revert-dialog').showModal(); $('cancel-revert').focus();
  });
  for (const id of ['close-revert','cancel-revert']) $(id).addEventListener('click', () => { if (!revertBusy) $('revert-dialog').close(); });
  $('revert-dialog').addEventListener('cancel', event => { if (revertBusy) event.preventDefault(); });
  $('revert-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!previewChange?.revertible || running() || busy || revertBusy) return;
    const selected = { ...previewChange }, epoch = workspaceRequestEpoch; revertBusy = true; busy = true; updateControls();
    try {
      const result = await api.request('revert_workspace_change', { change_id: selected.id, expected_revision: selected.after_revision });
      if (epoch !== workspaceRequestEpoch) return;
      if (result.state) applyState(result.state);
      $('revert-dialog').close(); previewChange = null; $('change-preview').hidden = true; $('changes-list').hidden = false;
      if (result.changes) renderWorkspaceChanges(result.changes); else await loadWorkspaceChanges();
      toast('文件变更已回退'); workspaceDirty = true;
    } catch (error) { $('revert-error').textContent = changeReason(error.message); $('revert-error').hidden = false; }
    finally { revertBusy = false; busy = false; updateControls(); }
  });
  addEventListener('resize', () => { if (!$('workbench').classList.contains('ledger-hidden') && innerWidth < 1150) onWorkspacePanelShown(); });
  selectWorkspaceTab('execution');
}
