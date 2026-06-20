interface DiffFile {
  path: string;
  lines: string[];
}

function parseDiffFiles(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;

  for (const line of diff.split('\n')) {
    const header = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    if (header) {
      current = { path: header[2], lines: [] };
      files.push(current);
      continue;
    }
    if (current) current.lines.push(line);
  }

  if (files.length === 0 && diff.trim()) {
    return [{ path: 'changes', lines: diff.split('\n') }];
  }
  return files;
}

function DiffLine({ line, lineNumber }: { line: string; lineNumber: number }) {
  let className = 'text-zinc-300';
  if (line.startsWith('+++') || line.startsWith('---')) className = 'text-sky-300';
  else if (line.startsWith('@@')) className = 'text-violet-300';
  else if (line.startsWith('+')) className = 'text-emerald-400 bg-emerald-950/40';
  else if (line.startsWith('-')) className = 'text-red-400 bg-red-950/40';

  return (
    <div className={`flex gap-2 px-2 ${className}`}>
      <span className="w-8 shrink-0 text-right text-zinc-600 select-none tabular-nums">{lineNumber}</span>
      <span className="flex-1 whitespace-pre-wrap break-all">{line || ' '}</span>
    </div>
  );
}

export function DiffViewer({ diff, title = 'pending diff' }: { diff: string; title?: string }) {
  const files = parseDiffFiles(diff);

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 border-b border-zinc-800 text-xs font-mono text-zinc-400">
        {title}
      </div>
      <div className="bg-zinc-950 max-h-72 overflow-auto font-mono text-[11px] leading-relaxed">
        {files.map((file) => (
          <div key={file.path} className="border-b border-zinc-900 last:border-0">
            <div className="sticky top-0 px-3 py-1.5 bg-zinc-900/95 text-[11px] text-sky-300 font-mono border-b border-zinc-800">
              {file.path}
            </div>
            {file.lines.map((line, index) => (
              <DiffLine key={`${file.path}-${index}`} line={line} lineNumber={index + 1} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
