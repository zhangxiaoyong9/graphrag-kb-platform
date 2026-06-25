import { useState } from "react";
import { useParams } from "react-router-dom";
import { useJobPolling } from "../hooks/useJobPolling";
import StepTimeline from "../components/StepTimeline";
import UnitTable from "../components/UnitTable";
import StatusBadge from "../components/StatusBadge";
import { retryStep } from "../api/client";

export default function JobDetailPage() {
  const { jobId } = useParams();
  const id = Number(jobId);
  const job = useJobPolling(id);
  const [selected, setSelected] = useState<number | null>(null);
  if (!job) return <div className="p-4">loading…</div>;
  const step = job.steps.find((s) => s.id === selected) ?? null;
  return (
    <div className="p-4 grid grid-cols-2 gap-4">
      <div><h1 className="text-xl font-bold">Job {job.id} <StatusBadge status={job.status} /></h1>
        <StepTimeline steps={job.steps} selected={selected} onSelect={setSelected} /></div>
      <div><h2 className="font-semibold flex items-center gap-2">{step ? step.name : "select a step"}{step && step.status === "partially_failed" && (<button onClick={async () => { await retryStep(step.id); }} className="bg-yellow-600 text-white px-2 py-0.5 rounded text-sm">Retry failed units</button>)}</h2>{step && <UnitTable stepId={step.id} active={job.status === "running"} />}</div>
    </div>
  );
}
