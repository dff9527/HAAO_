import { ChevronDown, ChevronUp, Plus, X } from 'lucide-react';
import { modelDisplayLabel, modelIdStatusDotClass } from '../modelDisplay';
import { isCloudModel } from '../constants';
import type { CloudModel } from '../types';

interface Props {
  chain: string[];
  localOptions: string[];
  cloudModels: CloudModel[];
  onChange: (chain: string[]) => void;
}

export function DevFallbackChainEditor({ chain, localOptions, cloudModels, onChange }: Props) {
  const availableLocal = localOptions.filter((model) => !chain.includes(model));
  const availableCloud = cloudModels.filter((model) => !chain.includes(model.id));
  const hasCloudInChain = chain.some((model) => isCloudModel(model));

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

  function modelLabel(model: string): string {
    return modelDisplayLabel(model, cloudModels);
  }

  return (
    <div className="space-y-2">
      {chain.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No models in fallback chain yet.</p>
      ) : (
        <ol className="space-y-1.5">
          {chain.map((model, index) => (
            <li
              key={`${model}-${index}`}
              className="flex items-center gap-1.5 rounded border border-border bg-background px-2 py-1"
            >
              <span className="w-4 text-[10px] font-mono text-muted-foreground shrink-0">{index + 1}</span>
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${modelIdStatusDotClass(model)}`}
                aria-hidden
              />
              <span className="flex-1 min-w-0">
                <span className="text-xs text-foreground truncate block">{modelLabel(model)}</span>
                <span className="font-mono text-[10px] text-muted-foreground truncate block" title={model}>
                  {model}
                </span>
              </span>
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
      {(availableLocal.length > 0 || availableCloud.length > 0) ? (
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
            <option value="">Add model…</option>
            {availableLocal.length > 0 && (
              <optgroup label="Local">
                {availableLocal.map((model) => (
                  <option key={model} value={model}>{modelDisplayLabel(model, cloudModels)}</option>
                ))}
              </optgroup>
            )}
            {availableCloud.length > 0 && (
              <optgroup label="Cloud (billable)">
                {availableCloud.map((model) => (
                  <option key={model.id} value={model.id}>
                    {modelDisplayLabel(model.id, cloudModels)}{model.key_configured ? '' : ' (no key)'}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          {(availableLocal[0] ?? availableCloud[0]) && (
            <button
              type="button"
              onClick={() => add(availableLocal[0] ?? availableCloud[0].id)}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-border hover:bg-muted"
            >
              <Plus size={11} /> Add
            </button>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">All available models are already in the chain.</p>
      )}
      {hasCloudInChain && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300 leading-relaxed">
          Cloud models in this chain incur API spend on every execution attempt. Costs appear in History — there is no spend cap.
        </p>
      )}
      <p className="text-[11px] text-muted-foreground leading-relaxed">
        Retry budget exhaustion tries the next model before escalating to cloud.
      </p>
    </div>
  );
}
