import { useEffect, useState } from "react";
import { getUnits, retryUnit } from "../api/client";
import type { UnitOut } from "../api/types";
import { cn } from "../lib/cn";
import { short } from "../lib/format";
import StatusBadge from "./StatusBadge";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "pending", label: "待处理" },
  { key: "running", label: "运行中" },
  { key: "succeeded", label: "成功" },
  { key: "failed", label: "失败" },
];

/** Per-step unit list with status filter + per-unit retry. Polls while active. */
export default function UnitTable({ stepId, active }: { stepId: number | null; active: boolean }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [filter, setFilter] = useState("");

  const reload = () => {
    if (stepId != null) getUnits(stepId, filter || undefined).then(setUnits).catch(() => {});
  };
  useEffect(reload, [stepId, filter]);
  useEffect(() => {
    if (active) {
      const h = setInterval(reload, 2000);
      return () => clearInterval(h);
    }
  }, [active, stepId]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={cn(
              "rounded-full border px-3 py-1 text-[13px] transition-colors",
              filter === f.key
                ? "border-brand bg-brand text-white"
                : "border-line-strong bg-surface text-body hover:bg-surface-2",
            )}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {units.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-[13px] text-muted">
          没有匹配的 unit
        </p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-2 text-left text-[12px] uppercase tracking-wide text-muted">
                <th className="px-3 py-2 font-medium">Unit</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">操作</th>
                <th className="px-3 py-2 font-medium">错误</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {units.map((u) => (
                <tr key={u.id} className="hover:bg-surface-2/50">
                  <td className="px-3 py-2 font-mono text-[12px] text-body">{short(u.subject_id)}</td>
                  <td className="px-3 py-2"><StatusBadge status={u.status} /></td>
                  <td className="px-3 py-2">
                    {u.status === "failed" && (
                      <button
                        onClick={async () => {
                          await retryUnit(u.id);
                          reload();
                        }}
                        className="text-[13px] font-medium text-brand hover:underline"
                      >
                        重试
                      </button>
                    )}
                  </td>
                  <td className="max-w-[280px] px-3 py-2 text-[12px] text-muted">
                    {u.error && (
                      <details>
                        <summary className="cursor-pointer text-danger hover:underline">查看错误</summary>
                        <pre className="mt-1 whitespace-pre-wrap rounded-lg bg-danger-soft/60 p-2 text-[11px] text-danger">
                          {u.error}
                        </pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
