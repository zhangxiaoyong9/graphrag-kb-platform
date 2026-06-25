import { useState } from "react";
import { useParams } from "react-router-dom";
import { useJobPolling } from "../hooks/useJobPolling";
import StepTimeline from "../components/StepTimeline";
import UnitTable from "../components/UnitTable";
import StatusBadge from "../components/StatusBadge";

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
      <div><h2 className="font-semibold">{step ? step.name : "select a step"}</h2>{step && <UnitTable stepId={step.id} active={job.status === "running"} />}</div>
    </div>
  );
}
