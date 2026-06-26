export function ModelStatusLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" aria-hidden />
        Local model
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-violet-500 shrink-0" aria-hidden />
        Gatekeeper
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-amber-500 shrink-0" aria-hidden />
        Cloud (billable)
      </span>
    </div>
  );
}
