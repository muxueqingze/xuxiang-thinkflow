'use strict';

// Keep completed blocks in the scrollback DOM. Only the open block can gain new
// inline markup, list items, or a closing fence on the next streaming update.
function* markdownBlocks(source, offset) {
  const boundary = /^[ \t]*\r?\n|^ {0,3}(`{3,}|~{3,})[^\n]*/gm;
  boundary.lastIndex = offset;
  let start = offset, match;
  while ((match = boundary.exec(source))) {
    if (match.index > start) yield* paragraphBlocks(source.slice(start, match.index), match.index);
    if (match[1]) {
      const marker = match[1], lineEnd = boundary.lastIndex;
      if (lineEnd === source.length) {
        yield { kind: 'code', text: '', next: null };
        return;
      }
      const closing = new RegExp('^ {0,3}' + marker[0] + '{' + marker.length + ',}[ \\t]*\\r?$', 'gm');
      closing.lastIndex = lineEnd + 1;
      const end = closing.exec(source);
      const code = source.slice(lineEnd + 1, end ? end.index : source.length);
      const next = end && closing.lastIndex < source.length ? closing.lastIndex + 1 : null;
      yield { kind: 'code', text: end ? code.replace(/\r?\n$/, '') : code, next };
      if (next === null) return;
      boundary.lastIndex = next;
    }
    start = boundary.lastIndex;
  }
  if (start < source.length) yield* paragraphBlocks(source.slice(start), null);
}

function* paragraphBlocks(source, next) {
  const lines = source.replace(/\r?\n$/, '').split(/\r?\n/);
  let block = null;
  for (const line of lines) {
    const item = /^\s*([-*]|\d+\.)\s+(.*)$/.exec(line);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const kind = item ? (/^\d/.test(item[1]) ? 'ordered' : 'list') : heading ? 'heading' : 'paragraph';
    if (!block || block.kind !== kind || heading) {
      if (block) yield block;
      block = { kind, lines: [], next: null };
      if (kind === 'ordered') block.start = parseInt(item[1], 10);
    }
    block.lines.push(item ? item[2] : heading ? heading[1] : line);
  }
  block.next = next;
  yield block;
}

function updateTextNode(node, value) {
  if (value.startsWith(node.data)) {
    if (value.length > node.length) node.appendData(value.slice(node.length));
  } else node.replaceData(0, node.length, value);
}

function updateInline(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let index = 0, start = 0, match;
  function part(tag, value) {
    if (!value) return;
    let node = parent.childNodes[index++];
    if (node?.nodeName !== tag) {
      const replacement = tag === '#text' ? document.createTextNode('') : element(tag.toLowerCase());
      if (node) node.replaceWith(replacement); else parent.append(replacement);
      node = replacement;
    }
    if (tag === '#text') updateTextNode(node, value);
    else if (!node.firstChild) node.append(document.createTextNode(value));
    else updateTextNode(node.firstChild, value);
  }
  while ((match = pattern.exec(text))) {
    part('#text', text.slice(start, match.index));
    const isCode = match[0][0] === '`';
    part(isCode ? 'CODE' : 'STRONG', match[0].slice(isCode ? 1 : 2, isCode ? -1 : -2));
    start = pattern.lastIndex;
  }
  part('#text', text.slice(start));
  while (parent.childNodes.length > index) parent.lastChild.remove();
}

function makeMarkdownBlock(kind) {
  if (kind === 'code') {
    const node = element('div', 'code-block'), pre = element('pre'), code = element('code');
    const copy = element('button', 'code-copy', '复制代码');
    code.append(document.createTextNode(''));
    copy.addEventListener('click', () => api.copyText(code.textContent).then(() => toast('已复制代码')).catch(() => showError('无法写入剪贴板')));
    pre.append(code); node.append(copy, pre);
    return node;
  }
  if (kind === 'ordered' || kind === 'list') return element(kind === 'ordered' ? 'ol' : 'ul', 'markdown-list');
  return element('div', 'markdown-' + kind);
}

function updateMarkdown(record, content) {
  const body = record.body;
  if (!record.content || !content.startsWith(record.content)) {
    record.markdownOffset = 0;
    record.markdownCount = 0;
  }
  let index = record.markdownCount;
  for (const block of markdownBlocks(content, record.markdownOffset)) {
    let node = body.children[index];
    if (node?.dataset.kind !== block.kind) {
      const replacement = makeMarkdownBlock(block.kind);
      replacement.dataset.kind = block.kind;
      if (node) node.replaceWith(replacement); else body.append(replacement);
      node = replacement;
    }
    if (block.kind === 'code') updateTextNode(node.querySelector('code').firstChild, block.text);
    else if (block.kind === 'list' || block.kind === 'ordered') {
      if (block.kind === 'ordered' && node.start !== block.start) node.start = block.start;
      block.lines.forEach((line, i) => {
        let item = node.children[i];
        if (!item) { item = element('li'); node.append(item); }
        if (item.markdownSource !== line) {
          updateInline(item, line);
          item.markdownSource = line;
        }
      });
      while (node.children.length > block.lines.length) node.lastChild.remove();
    } else updateInline(node, block.lines.join('\n'));
    index++;
    if (block.next !== null) { record.markdownOffset = block.next; record.markdownCount = index; }
  }
  while (body.children.length > index) body.lastChild.remove();
}
