import { useState, KeyboardEvent } from 'react';
import { X, Plus, Loader2, TicketPlus } from 'lucide-react';
import { getModelMeta } from '../constants';
import type { Project, TicketType } from '../types';
import type { BackendManualTicketCreateRequest } from '../api/types';

const TICKET_TYPES: TicketType[] = ['feature', 'bugfix', 'refactor', 'test', 'chore'];

interface Props {
  onClose: () => void;
  projects: Project[];
  selectedProjectId: string;
  localModelIds: string[];
  projectPathReady?: boolean;
  onCreate: (payload: BackendManualTicketCreateRequest) => Promise<void>;
}

function ChipInput({
  chips,
  onAdd,
  onRemove,
  placeholder,
}: {
  chips: string[];
  onAdd: (value: string) => void;
  onRemove: (value: string) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState('');

  function commit() {
    const value = draft.trim();
    if (value && !chips.includes(value)) {
      onAdd(value);
      setDraft('');
    }
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      commit();
    }
    if (event.key === 'Backspace' && draft === '' && chips.length > 0) {
      onRemove(chips[chips.length - 1]);
    }
  }

  return (
    <div
      className="flex flex-wrap gap-1.5 items-center p-1.5 border border-border rounded-lg bg-muted/40 min-h-[36px] cursor-text"
      onClick={(event) => (event.currentTarget.querySelector('input') as HTMLInputElement)?.focus()}
    >
      {chips.map((chip) => (
        <span key={chip} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-card border border-border text-xs font-mono">
          {chip}
          <button type="button" onClick={() => onRemove(chip)} className="text-muted-foreground hover:text-foreground">
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKey}
        onBlur={commit}
        placeholder={chips.length === 0 ? placeholder : undefined}
        className="flex-1 min-w-[100px] bg-transparent text-xs font-mono outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}

export function NewTicketModal({
  onClose,
  projects,
  selectedProjectId,
  localModelIds,
  projectPathReady = true,
  onCreate,
}: Props) {
  const project = projects.find((item) => item.id === selectedProjectId);
  const modelOptions = localModelIds.length ? localModelIds : ['qwen3-coder-next'];

  const [title, setTitle] = useState('');
  const [type, setType] = useState<TicketType>('feature');
  const [projectId, setProjectId] = useState(selectedProjectId);
  const [targetFiles, setTargetFiles] = useState<string[]>([]);
  const [taskDescription, setTaskDescription] = useState('');
  const [constraints, setConstraints] = useState<string[]>([]);
  const [dodTests, setDodTests] = useState<string[]>([]);
  const [acceptanceCriteria, setAcceptanceCriteria] = useState<string[]>([]);
  const [assignedModel, setAssignedModel] = useState(modelOptions[0] ?? 'qwen3-coder-next');
  const [skipMachineTests, setSkipMachineTests] = useState(false);
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  async function handleSubmit() {
    if (!projectPathReady) {
      setError('Set a repository path in project settings before creating tickets.');
      return;
    }
    if (!title.trim() || !taskDescription.trim() || targetFiles.length === 0) {
      setError('Title, task description, and at least one target file are required.');
      return;
    }
    setError('');
    setIsSaving(true);
    try {
      await onCreate({
        project_id: projectId,
        title: title.trim(),
        type,
        target_files: targetFiles,
        task_description: taskDescription.trim(),
        constraints,
        dod_tests: skipMachineTests ? [] : dodTests,
        acceptance_criteria: acceptanceCriteria,
        assigned_model: assignedModel,
      });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not create ticket.');
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/30 dark:bg-black/50 z-50 backdrop-blur-[2px]" onClick={isSaving ? undefined : onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-6 pointer-events-none">
        <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-2xl max-h-[92vh] flex flex-col pointer-events-auto overflow-hidden transition-all duration-200">
          <div className="flex items-center gap-2.5 px-5 py-4 border-b border-border shrink-0">
            <TicketPlus size={15} className="text-primary shrink-0" />
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-semibold">New ticket</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Skip the planner — assign a local agent and go straight to Ready.
              </p>
            </div>
            <button
              onClick={onClose}
              disabled={isSaving}
              className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X size={14} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Project</label>
                <select
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                  className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  {projects.map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Type</label>
                <select
                  value={type}
                  onChange={(event) => setType(event.target.value as TicketType)}
                  className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring capitalize"
                >
                  {TICKET_TYPES.map((ticketType) => (
                    <option key={ticketType} value={ticketType}>{ticketType}</option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Title</label>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Fix add_one off-by-one"
                className="w-full text-sm bg-muted/40 border border-border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Task description</label>
              <textarea
                value={taskDescription}
                onChange={(event) => setTaskDescription(event.target.value)}
                rows={4}
                placeholder="What should the local coder change?"
                className="w-full text-sm bg-muted/40 border border-border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring leading-relaxed"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Target files</label>
              <ChipInput
                chips={targetFiles}
                onAdd={(value) => setTargetFiles((prev) => [...prev, value])}
                onRemove={(value) => setTargetFiles((prev) => prev.filter((item) => item !== value))}
                placeholder={`e.g. calc.py — ${project?.name ?? 'repo'} relative path`}
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Constraints</label>
              <ChipInput
                chips={constraints}
                onAdd={(value) => setConstraints((prev) => [...prev, value])}
                onRemove={(value) => setConstraints((prev) => prev.filter((item) => item !== value))}
                placeholder="Optional non-goals"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-medium text-muted-foreground">DoD test commands</label>
                <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={skipMachineTests}
                    onChange={(event) => setSkipMachineTests(event.target.checked)}
                    className="h-3.5 w-3.5"
                  />
                  Unverified (human acceptance only)
                </label>
              </div>
              <ChipInput
                chips={dodTests}
                onAdd={(value) => setDodTests((prev) => [...prev, value])}
                onRemove={(value) => setDodTests((prev) => prev.filter((item) => item !== value))}
                placeholder={skipMachineTests ? 'Skipped when unverified' : 'pytest tests/test_calc.py -q'}
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Acceptance criteria</label>
              <ChipInput
                chips={acceptanceCriteria}
                onAdd={(value) => setAcceptanceCriteria((prev) => [...prev, value])}
                onRemove={(value) => setAcceptanceCriteria((prev) => prev.filter((item) => item !== value))}
                placeholder="Optional human-readable checks"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Assigned local model</label>
              <select
                value={assignedModel}
                onChange={(event) => setAssignedModel(event.target.value)}
                className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {modelOptions.map((model) => (
                  <option key={model} value={model}>{getModelMeta(model).label}</option>
                ))}
              </select>
            </div>

            {error && (
              <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                {error}
              </div>
            )}
            {!projectPathReady && !error && (
              <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                Set a repository path in project settings before creating tickets.
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-border px-5 py-4 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={isSaving}
              className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={isSaving || !projectPathReady}
              className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
              {isSaving ? 'Creating…' : 'Create ticket'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
