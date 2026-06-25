import type { StepOut } from "../api/types";
import { cn } from "../lib/cn";
import { pct, stepLabel } from "../lib/format";
import { statusTone } from "../lib/status";
import StatusBadge from "./StatusBadge";

/**
 * Vertical pipeline timeline. Each step shows its Chinese label, the raw
 * workflow name (kept visible so it stays greppable), status, and a progress
 * bar with succeeded/total counts.
 */
export default function StepTimeline({
  steps,
  selected,
  onSelect,
}: {
  steps: StepOut[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <ol className="relative space-y-1">
      {steps.map((s, i) => {
        const p = s.progress;
        const percent = p ? pct(succeeded(p), p.total) : null;
        const isLast = i === steps.length - 1;
        const tone = statusTone(s.status);
        const dotCls = {
          success: "bg-success",
          danger: "bg-danger",
          warning: "bg-warning",
          info: "bg-info",
          neutral: "bg-neutral",
          brand: "bg-brand",
        }[tone];
        return (
          <li key={s.id} className="relative pl-7">
            {/* connector */}
            {!isLast && <span className="absolute left-[14px] top-7 h-[calc(100%-6px)] w-px bg-line" />}
            {/* node dot */}
            <span
              className={cn(
                "absolute left-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded-full ring-4 ring-surface",
                s.status === "running" ? dotCls + " animate-pulse-ring" : dotCls,
              )}
            />
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className={cn(
                "w-full rounded-xl border px-3 py-2.5 text-left transition-all",
                selected === s.id
                  ? "border-brand bg-brand-50/60 shadow-soft"
                  : "border-line bg-surface hover:border-line-strong hover:bg-surface-2",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-sm font-medium text-ink">{stepLabel(s.name)}</span>
                  <span className="ml-2 font-mono text-[11px] text-muted">{s.name}</span>
                </div>
                <StatusBadge status={s.status} />
              </div>
              {percent != null && (
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                  <div
                    className={cn("h-full rounded-full", dotCls)}
                    style={{ width: `${percent}%` }}
                  />
                </div>
              )}
              {p && (
                <div className="mt-1 text-[11px] text-muted nums">
                  <span className="text-success">{p.succeeded}</span>
                  <span className="mx-1">/</span>
                  <span className="text-danger">{p.failed}</span>
                  <span className="mx-1">/</span>
                  <span>{p.running}</span>
                  <span className="mx-1">/</span>
                  <span>{p.pending}</span>
                  <span className="ml-1 text-muted/70">· 共 {p.total}</span>
                </div>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function succeeded(p: { succeeded: number; total: number }): number {
  return p.succeeded;
}
