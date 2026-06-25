import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useJobPolling } from "../hooks/useJobPolling";
import StepTimeline from "../components/StepTimeline";
import UnitTable from "../components/UnitTable";
import StatusBadge from "../components/StatusBadge";
import { CostPanel } from "../components/CostPanel";
import { retryStep, getJobCost } from "../api/client";
import type { JobCost } from "../api/types";

export default function JobDetailPage() {
  const { id, jobId } = useParams();
  const kbId = Number(id);
  const id2 = Number(jobId);
  const job = useJobPolling(id2);
  const [selected, setSelected] = useState<number | null>(null);
  const [cost, setCost] = useState<JobCost | null>(null);
  const status = job?.status;

  // Fetch cost alongside the job poll; stop once the job is terminal so we
  // don't keep polling a static snapshot. Effect re-runs on status change,
  // and when status is terminal the interval is never scheduled.
  useEffect(() => {
    if (!kbId || !id2) return;
    let stop = false;
    const tick = () => getJobCost(kbId, id2).then((c) => { if (!stop) setCost(c); }).catch(() => {});
    tick();
    if (status && ["succeeded", "failed", "cancelled"].includes(status)) return;
    const h = setInterval(tick, 2000);
    return () => { stop = true; clearInterval(h); };
  }, [kbId, id2, status]);

  if (!job) return <div className="p-4">loading…</div>;
  const step = job.steps.find((s) => s.id === selected) ?? null;
  return (
    <div className="p-4 grid grid-cols-2 gap-4">
      <div><h1 className="text-xl font-bold">Job {job.id} <StatusBadge status={job.status} /></h1>
        <StepTimeline steps={job.steps} selected={selected} onSelect={setSelected} />
        {cost && <CostPanel totalUsd={cost.total_usd} byStep={cost.by_step} />}</div>
      <div><h2 className="font-semibold flex items-center gap-2">{step ? step.name : "select a step"}{step && step.status === "partially_failed" && (<button onClick={async () => { await retryStep(step.id); }} className="bg-yellow-600 text-white px-2 py-0.5 rounded text-sm">Retry failed units</button>)}</h2>{step && <UnitTable stepId={step.id} active={job.status === "running"} />}</div>
    </div>
  );
}
