import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getKb, listDocuments, listJobsByKb, triggerJob } from "../api/client";
import type { KbOut, DocumentOut } from "../api/types";
import DocumentUpload from "../components/DocumentUpload";
import StatusBadge from "../components/StatusBadge";

export default function KbDetailPage() {
  const { id } = useParams();
  const kbId = Number(id);
  const [kb, setKb] = useState<KbOut | null>(null);
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [jobs, setJobs] = useState<{ id: number; status: string }[]>([]);
  const reload = () => { getKb(kbId).then(setKb); listDocuments(kbId).then(setDocs); listJobsByKb(kbId).then(setJobs); };
  useEffect(() => { reload(); }, [kbId]);
  if (!kb) return <div className="p-4">loading…</div>;
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-xl font-bold">{kb.name} <span className="text-gray-500">({kb.method})</span></h1>
      <section><h2 className="font-semibold">Documents</h2><ul>{docs.map((d) => <li key={d.id}>{d.title}</li>)}</ul>
        <DocumentUpload kbId={kbId} onUploaded={reload} /></section>
      <section><h2 className="font-semibold">Jobs</h2>
        <button onClick={async () => { await triggerJob(kbId); reload(); }} className="bg-green-600 text-white px-3 py-1 rounded">Trigger Index</button>
        <ul>{jobs.map((j) => <li key={j.id}><Link to={`/kbs/${kbId}/jobs/${j.id}`}>job {j.id}</Link> <StatusBadge status={j.status} /></li>)}</ul>
      </section>
    </div>
  );
}
