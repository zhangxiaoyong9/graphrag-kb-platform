import type { KbOut, DocumentOut, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json() as Promise<T>;
}

export const listKbs = () => req<KbOut[]>("/kbs");
export const createKb = (b: KbCreate) => req<KbOut>("/kbs", { method: "POST", body: JSON.stringify(b) });
export const getKb = (id: number) => req<KbOut>(`/kbs/${id}`);
export const listDocuments = (kbId: number) => req<DocumentOut[]>(`/kbs/${kbId}/documents`);
export const addDocument = (kbId: number, b: DocumentCreate) => req<DocumentOut>(`/kbs/${kbId}/documents`, { method: "POST", body: JSON.stringify(b) });
export const listJobsByKb = (kbId: number) => req<{ id: number; status: string }[]>(`/kbs/${kbId}/jobs`);
export const triggerJob = (kbId: number, method = "standard", type = "full") => req<{ id: number; status: string }>(`/kbs/${kbId}/jobs`, { method: "POST", body: JSON.stringify({ method, type }) });
export const getJob = (id: number) => req<JobOut>(`/jobs/${id}`);
export const getSteps = (jobId: number) => req<StepOut[]>(`/jobs/${jobId}/steps`);
export const getUnits = (stepId: number, status?: string) => req<UnitOut[]>(`/steps/${stepId}/units` + (status ? `?status=${status}` : ""));
export const retryUnit = (id: number) => req<{ ok: boolean }>(`/units/${id}/retry`, { method: "POST" });
export const retryStep = (id: number) => req<{ reset: number }>(`/steps/${id}/retry`, { method: "POST" });
