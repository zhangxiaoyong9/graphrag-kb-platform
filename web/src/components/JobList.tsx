import { Link } from "react-router-dom";
import { cn } from "../lib/cn";
import StatusBadge from "./StatusBadge";
import { IconChevronRight, IconTask } from "./icons";

export interface JobRow {
  kbId: number;
  kbName?: string;
  id: number;
  status: string;
}

/** Compact, reusable job list (used on dashboard, KB jobs, and global jobs). */
export function JobList({ rows, emptyHint }: { rows: JobRow[]; emptyHint?: string }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-[13px] text-muted">
        {emptyHint ?? "暂无任务"}
      </div>
    );
  }
  return (
    <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
      {rows.map((r) => (
        <li key={`${r.kbId}-${r.id}`}>
          <Link
            to={`/kbs/${r.kbId}/jobs/${r.id}`}
            className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-brand">
              <IconTask width={16} height={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-ink nums">任务 #{r.id}</span>
                <StatusBadge status={r.status} />
              </div>
              {r.kbName && (
                <div className="truncate text-[12px] text-muted">{r.kbName}</div>
              )}
            </div>
            <IconChevronRight width={16} height={16} className="text-muted" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
