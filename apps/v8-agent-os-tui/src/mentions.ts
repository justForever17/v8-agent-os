import path from 'node:path';

export type AtReference = { raw: string; value: string; kind: 'file' | 'session' | 'mcp' | 'extension' | 'url' };

/** Parse @ references without treating email addresses or ordinary prose as files. */
export function parseAtReferences(text: string): AtReference[] {
  const result: AtReference[] = [];
  for (let index = 0; index < text.length; index++) {
    if (text[index] !== '@' || index > 0 && !/\s/.test(text[index - 1] || '')) continue;
    let end = index + 1;
    let value = '';
    if (text[end] === '"') {
      end++;
      const start = end;
      while (end < text.length && text[end] !== '"') end++;
      value = text.slice(start, end);
      if (text[end] === '"') end++;
    } else {
      const start = end;
      while (end < text.length && !/\s/.test(text[end] || '')) end++;
      value = text.slice(start, end).replace(/[),;!?]+$/, '').replace(/\.$/, '');
    }
    if (!value) continue;
    const kind = /^https?:\/\//i.test(value) ? 'url'
      : value.startsWith('session:') ? 'session'
        : value.startsWith('mcp:') ? 'mcp'
          : value.startsWith('ext:') ? 'extension' : 'file';
    result.push({ raw: text.slice(index, end), value, kind });
    index = end - 1;
  }
  return result;
}

export function workspaceReferencePath(workspace: string, value: string): string {
  const root = path.resolve(workspace);
  const resolved = path.resolve(root, value);
  const relative = path.relative(root, resolved);
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`@ 文件必须位于当前工作区内：${value}`);
  }
  return resolved;
}
