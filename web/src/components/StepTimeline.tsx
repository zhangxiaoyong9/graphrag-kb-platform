import type { StepOut } from "../api/types";
import StatusBadge from "./StatusBadge";
export default function StepTimeline({ steps, selected, onSelect }: { steps: StepOut[]; selected: number | null; onSelect: (id: number) => void }) {
  return (
    <ol className="space-y-1">
      {steps.map((s) => {
        const p = s.progress;
        const pct = p && p.total ? Math.round((p.succeeded / p.total) * 100) : null;
        return (
          <li key={s.id} className={`p-2 border rounded cursor-pointer ${selected === s.id ? "border-blue-600" : ""}`} onClick={() => onSelect(s.id)}>
            <div className="flex items-center gap-2"><span className="font-medium">{s.name}</span> <StatusBadge status={s.status} /></div>
            {pct != null && <div className="h-2 bg-gray-200 rounded mt-1"><div className="h-2 bg-blue-600 rounded" style={{ width: `${pct}%` }} /></div>}
            {p && <div className="text-xs text-gray-500">{p.succeeded}/{p.total} units</div>}
          </li>
        );
      })}
    </ol>
  );
}
