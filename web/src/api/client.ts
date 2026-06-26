import type { KbOut, DocumentOut, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate, QueryResult, JobCost, KbCost, GraphData, Health } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json() as Promise<T>;
}

export const listKbs = () => req<KbOut[]>("/kbs");
export const createKb = (b: KbCreate) => req<KbOut>("/kbs", { method: "POST", body: JSON.stringify(b) });
export const getKb = (id: number) => req<KbOut>(`/kbs/${id}`);
export const updateKb = (id: number, body: { name: string; method: string; settings_yaml: string }) =>
  req<KbOut>(`/kbs/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const listDocuments = (kbId: number) => req<DocumentOut[]>(`/kbs/${kbId}/documents`);
export const addDocument = (kbId: number, b: DocumentCreate) => req<DocumentOut>(`/kbs/${kbId}/documents`, { method: "POST", body: JSON.stringify(b) });
export const uploadFile = async (kbId: number, file: File): Promise<DocumentOut> => {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/kbs/${kbId}/documents`, { method: "POST", body: form });
  if (!r.ok) throw new Error(`${r.status} /kbs/${kbId}/documents`);
  return r.json() as Promise<DocumentOut>;
};
export const deleteDocument = async (kbId: number, docId: number): Promise<void> => {
  const r = await fetch(`/kbs/${kbId}/documents/${docId}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`${r.status} /kbs/${kbId}/documents/${docId}`);
};
export const listJobsByKb = (kbId: number) => req<{ id: number; status: string }[]>(`/kbs/${kbId}/jobs`);
export const triggerJob = (kbId: number, method = "standard", type = "full") => req<{ id: number; status: string }>(`/kbs/${kbId}/jobs`, { method: "POST", body: JSON.stringify({ method, type }) });
export const getJob = (id: number) => req<JobOut>(`/jobs/${id}`);
export const getSteps = (jobId: number) => req<StepOut[]>(`/jobs/${jobId}/steps`);
export const getUnits = (stepId: number, status?: string) => req<UnitOut[]>(`/steps/${stepId}/units` + (status ? `?status=${status}` : ""));
export const retryUnit = (id: number) => req<{ ok: boolean }>(`/units/${id}/retry`, { method: "POST" });
export const retryStep = (id: number) => req<{ reset: number }>(`/steps/${id}/retry`, { method: "POST" });
export const query = (kbId: number, method: string, q: string) =>
  req<QueryResult>(`/kbs/${kbId}/query`, { method: "POST", body: JSON.stringify({ method, query: q }) });
export const getJobCost = (kbId: number, jobId: number) => req<JobCost>(`/kbs/${kbId}/jobs/${jobId}/cost`);
export const getKbCost = (kbId: number) => req<KbCost>(`/kbs/${kbId}/cost`);
export const getGraph = (kbId: number, params?: { limit?: number; q?: string; hop?: number }) => {
  const qs = new URLSearchParams();
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.q !== undefined && params.q !== "") qs.set("q", params.q);
  if (params?.hop !== undefined) qs.set("hop", String(params.hop));
  const tail = qs.toString();
  return req<GraphData>(`/kbs/${kbId}/graph${tail ? `?${tail}` : ""}`);
};

export const getHealth = () => req<Health>("/health");

export interface PromptDefaults {
  extract_graph: string;
  summarize_descriptions: string;
  community_reports: string;
}
export const getPromptDefaults = () => req<PromptDefaults>("/prompts/defaults");
