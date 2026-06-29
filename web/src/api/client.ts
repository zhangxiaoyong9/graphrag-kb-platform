import type { KbOut, KbDetail, DocumentOut, DocumentDetail, EvidenceDetail, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate, QueryResult, JobCost, KbCost, GraphData, Health, ProviderProfile, ProfileCreate, KbStats, Conversation, ConversationDetail } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  if (r.status === 204 || r.headers.get("content-length") === "0") return undefined as T;
  return r.json() as Promise<T>;
}

export const listKbs = () => req<KbOut[]>("/kbs");
export const createKb = (b: KbCreate) => req<KbOut>("/kbs", { method: "POST", body: JSON.stringify(b) });
export const getKb = (id: number) => req<KbDetail>(`/kbs/${id}`);
export const updateKb = (id: number, body: { name: string; method: string; settings_yaml: string; llm_profile_id: number; embedding_profile_id?: number | null }) =>
  req<KbOut>(`/kbs/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const listProfiles = (kind?: "llm" | "embedding") =>
  req<ProviderProfile[]>(`/provider-profiles${kind ? `?kind=${kind}` : ""}`);
export const createProfile = (b: ProfileCreate) =>
  req<ProviderProfile>("/provider-profiles", { method: "POST", body: JSON.stringify(b) });
export const updateProfile = (id: number, b: Partial<ProfileCreate>) =>
  req<ProviderProfile>(`/provider-profiles/${id}`, { method: "PATCH", body: JSON.stringify(b) });
export const deleteProfile = (id: number) => req<void>(`/provider-profiles/${id}`, { method: "DELETE" });
export const listDocuments = (kbId: number) => req<DocumentOut[]>(`/kbs/${kbId}/documents`);
export const getDocumentDetail = (kbId: number, docId: number) => req<DocumentDetail>(`/kbs/${kbId}/documents/${docId}`);
export const getDocumentEvidence = (kbId: number, docId: number, citationId: string) =>
  req<EvidenceDetail>(`/kbs/${kbId}/documents/${docId}/citations/${encodeURIComponent(citationId)}/evidence`);
export const addDocument = (kbId: number, b: DocumentCreate) => req<DocumentOut>(`/kbs/${kbId}/documents`, { method: "POST", body: JSON.stringify(b) });
export const uploadFile = async (kbId: number, file: File): Promise<DocumentOut> => {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/kbs/${kbId}/documents`, { method: "POST", body: form });
  if (!r.ok) throw new Error(`${r.status} /kbs/${kbId}/documents`);
  return r.json() as Promise<DocumentOut>;
};
export interface DeleteResult {
  /** true when the server auto-created an incremental shrink job (HTTP 202). */
  shrinkJobCreated: boolean;
  jobId?: number;
}

export const deleteDocument = async (kbId: number, docId: number): Promise<DeleteResult> => {
  const r = await fetch(`/kbs/${kbId}/documents/${docId}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`${r.status} /kbs/${kbId}/documents/${docId}`);
  if (r.status === 202) {
    const body = (await r.json()) as { id: number; status: string };
    return { shrinkJobCreated: true, jobId: body.id };
  }
  return { shrinkJobCreated: false };
};
export const listJobsByKb = (kbId: number) => req<{ id: number; status: string }[]>(`/kbs/${kbId}/jobs`);
export const triggerJob = (kbId: number, method = "standard", type = "full") => req<{ id: number; status: string }>(`/kbs/${kbId}/jobs`, { method: "POST", body: JSON.stringify({ method, type }) });
export const getJob = (id: number) => req<JobOut>(`/jobs/${id}`);
export const getSteps = (jobId: number) => req<StepOut[]>(`/jobs/${jobId}/steps`);
export interface UnitPage { items: UnitOut[]; total: number }
export const getUnits = (stepId: number, opts: { status?: string; limit?: number; offset?: number } = {}) => {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (opts.limit != null) qs.set("limit", String(opts.limit));
  if (opts.offset != null) qs.set("offset", String(opts.offset));
  const tail = qs.toString();
  return req<UnitPage>(`/steps/${stepId}/units${tail ? `?${tail}` : ""}`);
};
export const retryUnit = (id: number) => req<{ ok: boolean }>(`/units/${id}/retry`, { method: "POST" });
export const retryStep = (id: number) => req<{ reset: number }>(`/steps/${id}/retry`, { method: "POST" });
export const query = (kbId: number, method: string, q: string) =>
  req<QueryResult>(`/kbs/${kbId}/query`, { method: "POST", body: JSON.stringify({ method, query: q }) });
export const listConversations = (kbId: number) => req<Conversation[]>(`/kbs/${kbId}/conversations`);
export const createConversation = (kbId: number, title?: string) =>
  req<Conversation>(`/kbs/${kbId}/conversations`, { method: "POST", body: JSON.stringify({ title: title ?? null }) });
export const getConversation = (id: number) => req<ConversationDetail>(`/conversations/${id}`);
export const renameConversation = (id: number, title: string) =>
  req<Conversation>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const deleteConversation = (id: number) => req<void>(`/conversations/${id}`, { method: "DELETE" });
export const sendMessage = (convId: number, content: string, method?: string) =>
  fetch(`/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, method: method ?? null }),
  });
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
export const getKbStats = (kbId: number) => req<KbStats>(`/kbs/${kbId}/stats`);

export interface PromptDefaults {
  extract_graph: string;
  summarize_descriptions: string;
  community_reports: string;
  local_system: string;
  global_map: string;
  global_reduce: string;
  basic_system: string;
}
export const getPromptDefaults = () => req<PromptDefaults>("/prompts/defaults");
