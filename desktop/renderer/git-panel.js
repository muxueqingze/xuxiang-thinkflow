'use strict';
let changesSource = 'git', gitStatusRequest = 0, gitDirty = true;
let gitRows = new Map();

function resetGitPanel() {
  gitStatusRequest++;
  gitRows.clear();
  gitDirty = true;
  $('git-file-list').replaceChildren();
  $('git-branch').textContent = 'Git 工作区';
  $('git-feedback').textContent = '';
}

function selectChangesSource(source) {
  changesSource = source;
  $('source-git').setAttribute('aria-pressed', String(source === 'git'));
  $('source-agent').setAttribute('aria-pressed', String(source === 'agent'));
  $('git-changes').hidden = source !== 'git';
  $('agent-changes').hidden = source !== 'agent';
  if (source === 'git' && gitDirty) loadGitStatus();
  if (source === 'agent' && workspaceDirty && !previewChange) loadWorkspaceChanges();
}

function renderDiff(target, diff, lineNumbers = false) {
  const fragment = document.createDocumentFragment();
  let oldLine = 0, newLine = 0, inHunk = false;
  for (const line of diff.split('\n')) {
    if (lineNumbers) {
      if (line.startsWith('diff --git ')) inHunk = false;
      const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (header) {
        oldLine = Number(header[1]);
        newLine = Number(header[2]);
        inHunk = true;
      }
    }
    let style = 'diff-line';
    if (line.startsWith('@@')) style += ' diff-hunk';
    else if (line.startsWith('+') && (inHunk || !line.startsWith('+++'))) style += ' diff-add';
    else if (line.startsWith('-') && (inHunk || !line.startsWith('---'))) style += ' diff-remove';
    if (!lineNumbers) {
      fragment.append(element('span', style, line));
      continue;
    }
    const row = element('span', style);
    if (inHunk) {
      if (line[0] === ' ' || line[0] === '-') row.dataset.oldLine = String(oldLine++);
      if (line[0] === ' ' || line[0] === '+') row.dataset.newLine = String(newLine++);
    }
    row.append(element('span', 'diff-text', line));
    fragment.append(row);
  }
  target.replaceChildren(fragment);
}

function makeGitRow(key, group, item) {
  const details = element('details', 'git-file-details');
  details.dataset.gitGroup = group;
  const summary = element('summary', 'file-entry git-file-summary');
  summary.dataset.gitPath = item.path;
  const staged = group === 'staged';
  summary.dataset.staged = String(staged);
  const badge = element('span', 'git-status');
  summary.append(badge, element('strong', '', item.path));
  const feedback = element('p', 'git-inline-feedback');
  feedback.setAttribute('role', 'status');
  const diff = element('pre', 'git-inline-diff');
  diff.setAttribute('aria-label', `Git 文件差异 ${item.path}`);
  diff.tabIndex = 0;
  diff.hidden = true;
  details.append(summary, feedback, diff);
  const row = { key, path: item.path, staged, details, summary, badge, feedback, diff,
    loaded: false, request: null };
  details.addEventListener('toggle', () => {
    if (details.open && !row.loaded && !row.request) loadGitDiff(row);
  });
  return row;
}

function renderGitStatus(result) {
  const list = $('git-file-list'), scrollTop = list.scrollTop;
  const nextRows = new Map(), fragment = document.createDocumentFragment();
  $('git-branch').textContent = result.is_repo ? `分支 · ${result.branch}` : 'Git 工作区';
  for (const [group, label] of [['staged', '已暂存'], ['unstaged', '未暂存'], ['untracked', '未跟踪']]) {
    const entries = result[group];
    if (!entries.length) continue;
    fragment.append(element('h3', 'git-group-title', `${label} · ${entries.length}`));
    for (const item of entries) {
      const key = `${group}:${item.path}`;
      const row = gitRows.get(key) || makeGitRow(key, group, item);
      row.summary.title = item.old_path ? `${item.old_path} → ${item.path}` : item.path;
      row.badge.textContent = item.status;
      nextRows.set(key, row);
      fragment.append(row.details);
    }
  }
  gitRows = nextRows;
  list.replaceChildren(fragment);
  list.scrollTop = scrollTop;
  $('git-feedback').textContent = !result.is_repo ? '这个目录还不是 Git 仓库。' : gitRows.size ? '' : '工作区干净，没有待提交的变更。';
  for (const row of gitRows.values()) if (row.details.open) loadGitDiff(row);
}

async function loadGitStatus() {
  if (!connected || !state.cwd) {
    $('git-feedback').textContent = '请先选择工作区。';
    return;
  }
  const request = ++gitStatusRequest, cwd = state.cwd;
  $('git-feedback').textContent = '正在读取变更…';
  try {
    const result = await api.request('git_status');
    if (request !== gitStatusRequest || cwd !== state.cwd) return;
    gitDirty = false;
    renderGitStatus(result);
  } catch (error) {
    if (request === gitStatusRequest && cwd === state.cwd) $('git-feedback').textContent = error.message;
  }
}

async function loadGitDiff(row) {
  const cwd = state.cwd, request = {};
  row.request = request;
  const isCurrent = () => row.request === request && gitRows.get(row.key) === row && cwd === state.cwd;
  row.feedback.textContent = '正在读取差异…';
  try {
    const result = await api.request('git_diff', { path: row.path, staged: row.staged });
    if (!isCurrent()) return;
    row.loaded = true;
    row.diff.hidden = !result.diff;
    if (result.diff) renderDiff(row.diff, result.diff, true);
    row.feedback.textContent = result.truncated ? '差异较长，当前显示前 100 万字符。' : result.diff ? '' : '没有文本差异。';
  } catch (error) {
    if (!isCurrent()) return;
    row.loaded = false;
    row.diff.hidden = true;
    row.feedback.textContent = error.message;
  } finally {
    if (row.request === request) row.request = null;
  }
}

function initializeGitPanel() {
  $('source-git').addEventListener('click', () => selectChangesSource('git'));
  $('source-agent').addEventListener('click', () => selectChangesSource('agent'));
  $('refresh-git').addEventListener('click', loadGitStatus);
}
