
'use strict';
let pendingInput = null, pendingReceiptStatus = '', legacyInputs = [], editingInput = null, attachments = [], receiptQueryBusy = false;
const inputStatusNames = { received: '已接收', queued: '待执行', running: '正在执行', completed: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '中断待核对' };
function loadInputContext() {
  pendingInput = readLocal('v08-pending:' + draftKey, null);
  pendingReceiptStatus = pendingInput ? '未确认接收结果，正在查询后端回执…' : '';
  const savedAttachments = readLocal('v08-attachments:' + draftKey, []);
  attachments = Array.isArray(savedAttachments) ? savedAttachments.filter(item => typeof item?.path === 'string' && typeof item.revision === 'string') : [];
  const oldQueue = readLocal('queue:' + draftKey, []), oldOutbox = readLocal('outbox:' + draftKey, null);
  legacyInputs = Array.isArray(oldQueue) ? oldQueue.filter(item => typeof item?.prompt === 'string') : [];
  if (oldOutbox && typeof oldOutbox.prompt === 'string' && !legacyInputs.some(item => item.id === oldOutbox.id)) legacyInputs = [{ ...oldOutbox, uncertain: true }, ...legacyInputs];
  $('legacy-input-notice').hidden = !legacyInputs.length;
  if (pendingInput) setTimeout(() => queryInputReceipt(), 0);
}
function saveAttachments() { return writeLocal('v08-attachments:' + draftKey, attachments); }
function renderAttachments() {
  const list = $('attachment-chips'); list.replaceChildren(); list.hidden = !attachments.length;
  for (const item of attachments) {
    const chip = element('div', 'attachment-chip'), open = element('button', '', item.path), remove = element('button', '', '×');
    open.title = `查看只读文件：${item.path}\n引用版本：${item.revision}`;
    open.addEventListener('click', () => { selectWorkspaceTab('files'); toggleLedger(true); openWorkspaceFile(item.path); });
    remove.setAttribute('aria-label', `移除文件引用：${item.path}`);
    remove.addEventListener('click', () => { const previous = attachments; attachments = attachments.filter(entry => entry.path !== item.path); if (!saveAttachments()) attachments = previous; renderAttachments(); });
    chip.append(open, remove); list.append(chip);
  }
}
function attachWorkspaceFile(file) {
  if (!file?.revision || !file.path) { showError('文件版本尚未读取，请先刷新预览。'); return; }
  if (attachments.length >= 8 && !attachments.some(item => item.path === file.path)) { showError('每条任务最多引用8个文件，请先移除现有引用。'); return; }
  const previous = attachments;
  attachments = [...attachments.filter(item => item.path !== file.path), { path: file.path, revision: file.revision }];
  if (!saveAttachments()) { attachments = previous; return; }
  renderAttachments(); toast('文件已引用到任务；发送时将核对版本'); $('prompt').focus();
}
function renderInputReceipt() {
  $('input-receipt-panel').hidden = !pendingInput;
  $('input-receipt-status').textContent = pendingReceiptStatus;
  $('query-input-receipt').disabled = receiptQueryBusy || !connected;
  $('retry-input').hidden = !pendingInput || pendingReceiptStatus !== '后端未查到接收记录，内容已保留。可重试同一提交，或继续核对。';
  $('retry-input').disabled = sending || !canRun();
  $('restore-unreceived-input').hidden = $('retry-input').hidden;
  $('restore-unreceived-input').disabled = sending || receiptQueryBusy;
}
async function acceptInputReceipt(receipt, key, commandId) {
  if (!receipt || key !== draftKey || pendingInput?.command_id !== commandId) return false;
  if (!writeLocal('v08-pending:' + key, null)) {
    pendingReceiptStatus = `后端已确认：${inputStatusNames[receipt.status] || receipt.status}。本机待确认标记保存失败，请再次查询。`;
    renderInputReceipt(); return true;
  }
  pendingInput = null; pendingReceiptStatus = ''; renderInputReceipt(); updateControls();
  toast(`任务${inputStatusNames[receipt.status] || '已接收'}`); scheduleRefresh(); return true;
}
async function queryInputReceipt() {
  if (!pendingInput || receiptQueryBusy || !connected) return;
  const candidate = pendingInput, key = draftKey; receiptQueryBusy = true; renderInputReceipt();
  try {
    const result = await api.request('get_input_receipt', { command_id: candidate.command_id, session_id: candidate.session_id });
    if (key !== draftKey || pendingInput?.command_id !== candidate.command_id) return;
    if (!(await acceptInputReceipt(result?.receipt, key, candidate.command_id))) pendingReceiptStatus = '后端未查到接收记录，内容已保留。可重试同一提交，或继续核对。';
  } catch (error) {
    if (key === draftKey) pendingReceiptStatus = `暂时无法查询回执：${error.message}。内容已保留，恢复连接后再查询。`;
  } finally { receiptQueryBusy = false; renderInputReceipt(); updateControls(); }
}
async function submitPrompt(prompt, retry = false) {
  if (sending || !canRun()) return;
  const key = draftKey;
  const candidate = retry ? pendingInput : { command_id: crypto.randomUUID(), prompt, session_id: state.session_id, attachments: attachments.map(item => ({ ...item })) };
  if (!candidate) return;
  if (!retry) {
    if (!writeLocal('v08-pending:' + key, candidate)) return;
    pendingInput = candidate;
    $('prompt').value = '';
    if (!saveDraft()) { $('prompt').value = prompt; pendingInput = null; writeLocal('v08-pending:' + key, null); resizePrompt(); updateControls(); return; }
    attachments = []; saveAttachments(); renderAttachments(); resizePrompt();
  }
  sending = true; pendingReceiptStatus = '正在提交，等待后端确认接收…'; renderInputReceipt(); updateControls();
  try {
    const result = await api.request('submit_input', candidate);
    if (result?.state) applyState(result.state);
    if (!(await acceptInputReceipt(result?.receipt, key, candidate.command_id)) && key === draftKey) pendingReceiptStatus = '没有收到完整回执；内容已保留，正在精确查询。';
  } catch (error) {
    if (key === draftKey) pendingReceiptStatus = `发送回执未确认：${error.message}。正在查询原提交，不会另建任务。`;
  } finally {
    sending = false; renderInputReceipt(); updateControls();
    if (key === draftKey && pendingInput?.command_id === candidate.command_id) await queryInputReceipt();
  }
}
function renderQueue() {
  const waiting = queue.filter(item => item.status !== 'running');
  $('queue-panel').hidden = !waiting.length;
  $('queue-status').textContent = `${queue.some(item => item.status === 'running') ? '执行中 · ' : ''}待发 ${waiting.length} 条${queuePaused ? ' · 已暂停' : ''}`;
  $('resume-queue').hidden = !queuePaused; $('pause-queue').hidden = queuePaused;
  $('queue-reason').textContent = state.queue_pause_reason || '后续任务由本地后端保存；当前成功完成后继续，重启后暂停。';
  const list = $('queue-list'); list.replaceChildren();
  for (const [index,item] of waiting.entries()) {
    const row = element('li'), label = element('span', '', `${inputStatusNames[item.status] || '待执行'} · ${item.prompt || ''}`), id = item.id || item.command_id;
    row.dataset.commandId = id; label.title = item.prompt || '';
    const edit = element('button', 'queue-edit', '编辑'), remove = element('button', 'queue-remove', '移除');
    edit.disabled = remove.disabled = item.status === 'running' || busy || !connected;
    edit.addEventListener('click', () => openQueueEditor(item));
    remove.addEventListener('click', () => perform(() => api.request('update_input', { command_id: id, action: 'remove' })));
    row.append(label, edit, remove); list.append(row);
  }
}
function openQueueEditor(item) {
  editingInput = item; $('queue-edit-title').textContent = '编辑待发任务'; $('queue-edit-prompt').value = item.prompt || '';
  $('queue-edit-prompt').readOnly = false; $('save-queue-edit').hidden = false; $('queue-edit-error').hidden = true;
  $('queue-edit-note').textContent = '只修改尚未运行的任务；文件引用保持不变。';
  $('queue-edit-dialog').showModal(); $('queue-edit-prompt').focus();
}
function initializeInputController() {
  $('query-input-receipt').addEventListener('click', queryInputReceipt);
  $('retry-input').addEventListener('click', () => submitPrompt(pendingInput?.prompt, true));
  $('restore-unreceived-input').addEventListener('click', () => {
    if (!pendingInput || sending || receiptQueryBusy || $('restore-unreceived-input').hidden) return;
    if ($('prompt').value.trim()) { showError('输入框已有草稿，请先保存或清空后再取回。'); return; }
    const candidate = pendingInput; $('prompt').value = candidate.prompt;
    if (!saveDraft()) { $('prompt').value = ''; return; }
    attachments = candidate.attachments || []; saveAttachments();
    if (!writeLocal('v08-pending:' + draftKey, null)) { $('prompt').value = ''; saveDraft(); return; }
    pendingInput = null; pendingReceiptStatus = ''; renderInputReceipt(); renderAttachments(); resizePrompt(); updateControls(); $('prompt').focus();
  });
  $('edit-pending-input').addEventListener('click', () => {
    editingInput = null; $('queue-edit-title').textContent = '待确认提交内容'; $('queue-edit-prompt').value = pendingInput?.prompt || '';
    $('queue-edit-prompt').readOnly = true; $('save-queue-edit').hidden = true; $('queue-edit-error').hidden = true;
    $('queue-edit-note').textContent = '接收结果未确认；先查询回执。可选择内容复制，当前不会自动重发。'; $('queue-edit-dialog').showModal();
  });
  for (const id of ['close-queue-edit','cancel-queue-edit']) $(id).addEventListener('click', () => $('queue-edit-dialog').close());
  $('queue-edit-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!editingInput || !$('queue-edit-prompt').value.trim()) return;
    const id = editingInput.id || editingInput.command_id; $('save-queue-edit').disabled = true;
    try { applyState(await api.request('update_input', { command_id: id, action: 'edit', prompt: $('queue-edit-prompt').value })); $('queue-edit-dialog').close(); }
    catch (error) { $('queue-edit-error').textContent = error.message; $('queue-edit-error').hidden = false; }
    finally { $('save-queue-edit').disabled = false; }
  });
  $('legacy-input-notice').addEventListener('click', () => {
    const list = $('legacy-input-list'); list.replaceChildren();
    for (const item of legacyInputs) {
      const entry = element('section','legacy-input-entry'), body = element('pre','',item.prompt), copy = element('button','quiet-button','复制'), take = element('button','quiet-button','取回草稿');
      copy.addEventListener('click', () => api.copyText(item.prompt).then(() => toast('已复制旧版内容')).catch(error => showError(error.message)));
      take.addEventListener('click', () => { if ($('prompt').value.trim()) { showError('输入框已有草稿，请先保存或清空后再取回。'); return; } $('prompt').value = item.prompt; saveDraft(); resizePrompt(); updateControls(); $('legacy-input-dialog').close(); $('prompt').focus(); });
      entry.append(body,copy,take); list.append(entry);
    }
    $('legacy-input-dialog').showModal();
  });
  $('close-legacy-input').addEventListener('click', () => $('legacy-input-dialog').close());
  for (const id of ['queue-edit-dialog','legacy-input-dialog']) $(id).addEventListener('close', () => $('prompt').focus());
}
