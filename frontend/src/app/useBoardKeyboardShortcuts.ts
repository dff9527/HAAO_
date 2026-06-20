import { useEffect, type RefObject } from 'react';

interface Options {
  enabled: boolean;
  searchInputRef: RefObject<HTMLInputElement | null>;
  onCloseOverlay: () => void;
  onFocusSearch: () => void;
}

export function useBoardKeyboardShortcuts({
  enabled,
  searchInputRef,
  onCloseOverlay,
  onFocusSearch,
}: Options) {
  useEffect(() => {
    if (!enabled) return;

    function isTypingTarget(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseOverlay();
        return;
      }

      if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (isTypingTarget(event.target)) return;
        event.preventDefault();
        onFocusSearch();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled, onCloseOverlay, onFocusSearch, searchInputRef]);
}
