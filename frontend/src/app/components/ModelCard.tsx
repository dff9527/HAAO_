import { useState, useEffect } from 'react';
import {
  ChevronDown, ChevronRight, Loader2, AlertTriangle, RotateCcw, Save,
} from 'lucide-react';
import { getModelMeta } from '../constants';
import type { ModelConfig, ModelParams } from '../types';

function normalizeParams(params: ModelParams): ModelParams {
  const additionalInstructions = params.additionalInstructions || params.systemPrompt || '';
  return {
    ...params,
    additionalInstructions,
    fullPromptOverride: params.fullPromptOverride ?? '',
    useFullPromptOverride: params.useFullPromptOverride ?? false,
    systemPrompt: additionalInstructions,
  };
}

interface Props {
  config: ModelConfig;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onUpdate: (updates: Partial<ModelConfig>) => void;
  onLoadAdditionalInstructions?: (modelId: string) => Promise<string>;
  onSaveAdditionalInstructions?: (modelId: string, text: string) => Promise<string>;
}

export function ModelCard({
  config,
  isExpanded,
  onToggleExpand,
  onUpdate,
  onLoadAdditionalInstructions,
  onSaveAdditionalInstructions,
}: Props) {
  const [localParams, setLocalParams] = useState<ModelParams>(() => normalizeParams(config.params));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [instructionsLoaded, setInstructionsLoaded] = useState(false);
  const [savedInstructions, setSavedInstructions] = useState(
    () => normalizeParams(config.params).additionalInstructions,
  );
  const [instructionsError, setInstructionsError] = useState('');
  const [isSavingInstructions, setIsSavingInstructions] = useState(false);

  useEffect(() => {
    setLocalParams(normalizeParams(config.params));
    setShowAdvanced(false);
    setInstructionsLoaded(false);
    setSavedInstructions(normalizeParams(config.params).additionalInstructions);
    setInstructionsError('');
  }, [config.id, config.params]);

  useEffect(() => {
    if (!isExpanded || instructionsLoaded || !onLoadAdditionalInstructions) return;
    let cancelled = false;
    onLoadAdditionalInstructions(config.id)
      .then((text) => {
        if (cancelled) return;
        setLocalParams((prev) => normalizeParams({ ...prev, additionalInstructions: text }));
        setSavedInstructions(text);
        setInstructionsLoaded(true);
      })
      .catch((error) => {
        if (cancelled) return;
        setInstructionsError(error instanceof Error ? error.message : 'Could not load additional instructions.');
      });
    return () => {
      cancelled = true;
    };
  }, [config.id, instructionsLoaded, isExpanded, onLoadAdditionalInstructions]);

  const instructionsDirty = localParams.additionalInstructions !== savedInstructions;
  const meta = getModelMeta(config.id);

  async function handleSave() {
    const normalized = normalizeParams(localParams);
    setIsSavingInstructions(true);
    setInstructionsError('');
    try {
      if (onSaveAdditionalInstructions) {
        const saved = await onSaveAdditionalInstructions(config.id, normalized.additionalInstructions);
        normalized.additionalInstructions = saved;
      }
      onUpdate({
        params: {
          ...normalized,
          systemPrompt: normalized.additionalInstructions,
        },
      });
      setLocalParams(normalized);
      setSavedInstructions(normalized.additionalInstructions);
      setInstructionsLoaded(true);
    } catch (error) {
      setInstructionsError(error instanceof Error ? error.message : 'Could not save additional instructions.');
    } finally {
      setIsSavingInstructions(false);
    }
  }

  function handleReset() {
    setLocalParams(normalizeParams({
      ...config.params,
      additionalInstructions: savedInstructions || config.params.additionalInstructions,
    }));
  }

  return (
    <div className={`rounded-xl border bg-card transition-all ${
      isExpanded ? 'border-border shadow-sm' : 'border-border'
    }`}>
      <button
        type="button"
        className="w-full flex items-center gap-3 px-4 py-3 text-left group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl"
        onClick={onToggleExpand}
        aria-expanded={isExpanded}
      >
        <div className={`w-2 h-2 rounded-full shrink-0 ${
          config.backend === 'Cloud API' ? 'bg-amber-500' : 'bg-emerald-500'
        }`} />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-medium text-foreground">{config.name}</span>
            {instructionsDirty && (
              <span className="text-[10px] px-1.5 py-0 rounded bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-400 border border-amber-200 dark:border-amber-800">
                unsaved instructions
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <span className={`text-[11px] px-1.5 py-0 rounded border font-medium ${meta.pillClass}`}>
              {config.backend}
            </span>
            {config.quant && (
              <span className="font-mono text-[11px] text-muted-foreground bg-muted px-1.5 py-0 rounded">
                {config.quant}
              </span>
            )}
            {config.moeActive && (
              <span className="font-mono text-[11px] text-muted-foreground">MoE {config.moeActive}</span>
            )}
          </div>
        </div>

        <div className="shrink-0 text-muted-foreground group-hover:text-foreground transition-colors">
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-border px-4 pb-4 pt-3 space-y-3">
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            {config.backend === 'LM Studio (local)'
              ? 'Discovered from your local model server. Load and unload models in LM Studio — HAAO uses the endpoints configured above.'
              : 'Cloud model settings are managed under Connections. Additional instructions below are saved to the server.'}
          </p>

          <div className="space-y-1">
            <label className="text-xs font-medium text-foreground">Additional instructions</label>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Appended on top of HAAO&apos;s system prompt for this model. Saved to the server.
            </p>
            {!instructionsLoaded && onLoadAdditionalInstructions && (
              <p className="text-[11px] text-muted-foreground">Loading saved instructions…</p>
            )}
            {instructionsError && (
              <p className="text-[11px] text-red-600 dark:text-red-400">{instructionsError}</p>
            )}
            <textarea
              value={localParams.additionalInstructions}
              onChange={(e) => setLocalParams((prev) => normalizeParams({
                ...prev,
                additionalInstructions: e.target.value,
              }))}
              rows={4}
              className="w-full font-mono text-xs bg-muted border border-border rounded px-2 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-ring text-foreground leading-relaxed"
            />
          </div>

          <div className="pt-1">
            <button
              type="button"
              onClick={() => setShowAdvanced((value) => !value)}
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Advanced
            </button>
            {showAdvanced && (
              <div className="mt-2 rounded-lg border border-border bg-muted/30 p-3 space-y-2">
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <input
                    type="checkbox"
                    checked={localParams.useFullPromptOverride}
                    onChange={(e) => setLocalParams((prev) => ({
                      ...prev,
                      useFullPromptOverride: e.target.checked,
                    }))}
                    className="h-3.5 w-3.5"
                  />
                  Full prompt override
                </label>
                <p className="text-[11px] text-muted-foreground">
                  Not persisted yet — for future use only.
                </p>
                {localParams.useFullPromptOverride && (
                  <>
                    <p className="flex items-start gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                      <AlertTriangle size={11} className="shrink-0 mt-0.5" />
                      Replacing the full prompt may break reliability and is unsupported.
                    </p>
                    <textarea
                      value={localParams.fullPromptOverride}
                      onChange={(e) => setLocalParams((prev) => ({
                        ...prev,
                        fullPromptOverride: e.target.value,
                      }))}
                      rows={4}
                      className="w-full font-mono text-xs bg-muted border border-border rounded px-2 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-ring text-foreground leading-relaxed"
                    />
                  </>
                )}
              </div>
            )}
          </div>

          {instructionsDirty && (
            <div className="flex items-center gap-2 pt-2 border-t border-border">
              <span className="text-xs text-amber-600 dark:text-amber-400 flex-1">Unsaved instructions</span>
              <button
                type="button"
                onClick={handleReset}
                className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <RotateCcw size={10} /> Reset
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={isSavingInstructions}
                className="flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {isSavingInstructions ? <Loader2 size={10} className="animate-spin" /> : <Save size={10} />}
                Save instructions
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
