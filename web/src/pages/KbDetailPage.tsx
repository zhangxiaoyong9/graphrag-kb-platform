import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getKb, listDocuments, listJobsByKb, triggerJob, query as apiQuery, getKbCost } from "../api/client";
import type { KbOut, DocumentOut, KbCost } from "../api/types";
import { DocumentManager } from "../components/DocumentManager";
import StatusBadge from "../components/StatusBadge";
import { CostPanel } from "../components/CostPanel";

export default function KbDetailPage() {
  const { id } = useParams();
  const kbId = Number(id);
  const [kb, setKb] = useState<KbOut | null>(null);
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [jobs, setJobs] = useState<{ id: number; status: string }[]>([]);
  const [qMethod, setQMethod] = useState("local");
  const [qText, setQText] = useState("");
  const [qAnswer, setQAnswer] = useState("");
  const [cost, setCost] = useState<KbCost | null>(null);
  const reload = () => {
    getKb(kbId).then(setKb);
    listDocuments(kbId).then(setDocs);
    listJobsByKb(kbId).then(setJobs);
    getKbCost(kbId).then(setCost).catch(() => {});
  };
  useEffect(() => { reload(); }, [kbId]);
  if (!kb) return <div className="p-4">loading…</div>;
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-xl font-bold">{kb.name} <span className="text-gray-500">({kb.method})</span></h1>
      <section>
        <h2 className="font-semibold">Documents</h2>
        <DocumentManager kbId={kbId} docs={docs} reload={reload} />
      </section>
      <section><h2 className="font-semibold">Jobs</h2>
        <button onClick={async () => { await triggerJob(kbId); reload(); }} className="bg-green-600 text-white px-3 py-1 rounded">Trigger Index</button>
        <ul>{jobs.map((j) => <li key={j.id}><Link to={`/kbs/${kbId}/jobs/${j.id}`}>job {j.id}</Link> <StatusBadge status={j.status} /></li>)}</ul>
      </section>
      {cost && (
        <section>
          <h2 className="font-semibold">Cumulative Cost</h2>
          <CostPanel totalUsd={cost.total_usd} byStep={cost.by_step} />
        </section>
      )}
      <section>
        <h2 className="font-semibold">Query</h2>
        <div className="flex gap-2 items-center">
          <select aria-label="query method" value={qMethod} onChange={(e) => setQMethod(e.target.value)} className="border p-1 rounded">
            <option value="local">local</option>
            <option value="global">global</option>
            <option value="drift">drift</option>
            <option value="basic">basic</option>
          </select>
          <input className="border p-1 flex-1" value={qText} onChange={(e) => setQText(e.target.value)} placeholder="ask a question" />
          <button onClick={async () => { const r = await apiQuery(kbId, qMethod, qText); setQAnswer(r.answer); }} className="bg-blue-600 text-white px-3 py-1 rounded">Ask</button>
        </div>
        {qAnswer && <div className="mt-2 p-2 bg-gray-50 rounded whitespace-pre-wrap">{qAnswer}</div>}
      </section>
    </div>
  );
}
