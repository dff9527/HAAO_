import { ChevronDown, ChevronUp, Plus, X } from 'lucide-react';
import { getModelMeta } from '../constants';

interface Props {
  chain: string[];
  options: string[];
  onChange: (chain: string[]) => void;
}

export function DevFallbackChainEditor({ chain, options, onChange }: Props) {
  const available = options.filter((model) => !chain.includes(model));

  function move(index: number, direction: -1 | 1) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= chain.length) return;
    const next = [...chain];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    onChange(next);
  }

  function remove(index: number) {
    onChange(chain.filter((_, itemIndex) => itemIndex !== index));
  }

  function add(model: string) {
    if (!model || chain.includes(model)) return;
    onChange([...chain, model]);
  }

  return (
    <div className="space-y-2">
      {chain.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No local models in fallback chain yet.</p>
      ) : (
        <ol className="space-y-1.5">
          {chain.map((model, index) => (
            <li
              key={`${model}-${index}`}
              className="flex items-center gap-1.5 rounded border border-border bg-background px-2 py-1"
            >
              <span className="w-4 text-[10px] font-mono text-muted-foreground shrink-0">{index + 1}</span>
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ backgroundColor: getModelMeta(model).accent }}
              />
              <span className="flex-1 text-xs font-mono truncate">{getModelMeta(model).label}</span>
              <div className="flex items-center gap-0.5 shrink-0">
                <button
                  type="button"
                  onClick={() => move(index, -1)}
                  disabled={index === 0}
                  className="h-6 w-6 flex items-center justify-center rounded hover:bg-muted disabled:opacity-30"
                  title="Move up"
                >
                  <ChevronUp size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => move(index, 1)}
                  disabled={index === chain.length - 1}
                  className="h-6 w-6 flex items-center justify-center rounded hover:bg-muted disabled:opacity-30"
                  title="Move down"
                >
                  <ChevronDown size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => remove(index)}
                  className="h-6 w-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground"
                  title="Remove"
                >
                  <X size={12} />
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {available.length > 0 ? (
        <div className="flex items-center gap-2">
          <select
            defaultValue=""
            onChange={(event) => {
              const value = event.target.value;
              if (value) add(value);
              event.currentTarget.value = '';
            }}
            className="flex-1 text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">Add local model…</option>
            {available.map((model) => (
              <option key={model} value={model}>{getModelMeta(model).label}</option>
            ))}
          </select>
          {available[0] && (
            <button
              type="button"
              onClick={() => add(available[0])}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-border hover:bg-muted"
            >
              <Plus size={11} /> Add
            </button>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">All discovered models are already in the chain.</p>
      )}
      <p className="text-[11px] text-muted-foreground leading-relaxed">
        Retry budget exhaustion tries the next model before escalating to cloud.
      </p>
    </div>
  );
}
