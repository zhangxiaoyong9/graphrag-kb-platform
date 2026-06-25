import { useEffect, useState } from "react";
import { getUnits, retryUnit } from "../api/client";
import type { UnitOut } from "../api/types";
import StatusBadge from "./StatusBadge";
export default function UnitTable({ stepId, active }: { stepId: number | null; active: boolean }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [filter, setFilter] = useState("");
  const reload = () => { if (stepId != null) getUnits(stepId, filter || undefined).then(setUnits); };
  useEffect(reload, [stepId, filter]);
  useEffect(() => { if (active) { const h = setInterval(reload, 2000); return () => clearInterval(h); } }, [active, stepId]);
  return (
    <div>
      <div className="flex gap-2 my-2">{["", "pending", "running", "succeeded", "failed"].map((f) => <button key={f} className={`px-2 py-0.5 rounded border ${filter === f ? "bg-blue-600 text-white" : ""}`} onClick={() => setFilter(f)}>{f || "all"}</button>)}</div>
      <table className="w-full text-sm"><tbody>
        {units.map((u) => <tr key={u.id} className="border-t"><td className="p-1 font-mono text-xs">{u.subject_id.slice(0, 12)}</td><td className="p-1"><StatusBadge status={u.status} /></td>
          <td className="p-1">{u.status === "failed" && <button onClick={async () => { await retryUnit(u.id); reload(); }} className="text-blue-600 underline">retry</button>}</td>
          <td className="p-1 text-xs text-gray-600">{u.error && <details><summary>error</summary><pre className="whitespace-pre-wrap">{u.error}</pre></details>}</td></tr>)}
      </tbody></table>
    </div>
  );
}
