import { X } from 'lucide-react';

export interface ToastMessage {
  id: string;
  text: string;
  tone: 'error' | 'info' | 'success';
}

interface Props {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}

export function ToastStack({ toasts, onDismiss }: Props) {
  if (!toasts.length) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[70] flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`rounded-lg border px-3 py-2.5 shadow-lg text-xs flex items-start gap-2 transition-all duration-200 ${
            toast.tone === 'error'
              ? 'bg-red-50 text-red-800 border-red-200 dark:bg-red-950 dark:text-red-200 dark:border-red-800'
              : toast.tone === 'success'
              ? 'bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-200 dark:border-emerald-800'
              : 'bg-card text-foreground border-border'
          }`}
        >
          <span className="flex-1 leading-relaxed">{toast.text}</span>
          <button
            onClick={() => onDismiss(toast.id)}
            className="text-muted-foreground hover:text-foreground shrink-0"
          >
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
