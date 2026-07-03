export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled" | "partially_failed";
export type StepStatus = "pending" | "running" | "succeeded" | "partially_failed" | "failed";
export type UnitStatus = "pending" | "running" | "succeeded" | "failed";

export interface KbOut { id: number; name: string; method: string; settings?: Record<string, unknown> }
export interface DocumentOut { id: number; title: string; status: string | null; bytes: number; chunk_count: number }
export interface DocumentCitation {
  id: string;
  label: string;
  snippet: string;
  chunk_id: string;
  ordinal: number;
}
export interface DocumentDetail extends DocumentOut {
  text: string;
  citations: DocumentCitation[];
}
export interface EvidenceContext {
  document_id: number;
  document_title: string;
  chunk_id: string;
  ordinal: number;
}
export interface EvidenceDetail {
  citation_id: string;
  matched: string;
  before: string | null;
  after: string | null;
  source: EvidenceContext;
}
export interface UnitProgress { pending: number; running: number; succeeded: number; failed: number; total: number }
export interface StepOut { id: number; name: string; ordinal: number; kind: string; status: StepStatus; progress: UnitProgress | null }
export interface JobOut { id: number; status: JobStatus; steps: StepOut[] }
export interface UnitOut { id: number; subject_id: string; status: UnitStatus; error: string | null; llm_raw_output: string | null; needs_reconsolidation: boolean; input_text: string | null }
export interface ProviderProfile {
  id: number;
  name: string;
  kind: "llm" | "embedding";
  provider: string;
  model: string;
  api_base: string | null;
  api_version: string | null;
  structured_output: boolean;
  api_keys_count: number;
  ssl_verify: boolean;
}
export interface ProfileCreate {
  name: string;
  kind: "llm" | "embedding";
  provider: string;
  model: string;
  api_base?: string | null;
  api_version?: string | null;
  api_keys: string[];
  structured_output: boolean;
  ssl_verify?: boolean;
}
export interface ProfileRef { id: number; name: string; provider: string; model: string }
export interface KbCreate {
  name: string;
  method?: string;
  settings_yaml?: string;
  llm_profile_id: number;
  embedding_profile_id?: number | null;
  min_unit_success_ratio?: number;
  llm_fallback_profile_ids?: number[];
}
export interface KbDetail extends KbOut {
  settings: Record<string, unknown>;
  llm_profile: ProfileRef | null;
  embedding_profile: ProfileRef | null;
  llm_fallback_profile_ids?: number[];
  llm_fallback_profiles?: ProfileRef[];
}
export interface DocumentCreate { title: string; text: string }

export interface SourceRef { kind: string; name: string; text: string }

export interface QueryResult {
  answer: string;
  method: string;
  error: string | null;
  elapsed_ms?: number;
  prompt_tokens?: number;
  output_tokens?: number;
  llm_calls?: number;
  sources?: SourceRef[];
  truncated?: boolean;
  cypher?: string | null;
}

export interface CostItem { model: string; prompt_tokens: number; completion_tokens: number; usd: number | null }
export interface JobCost { total_usd: number | null; by_step: Record<string, number>; by_model: Record<string, CostItem> }
export interface KbCost extends JobCost { by_job: Record<string, number> }

export interface GraphNode { id: string; title: string; type: string; degree: number; community: string }
export interface GraphEdge { source: string; target: string; weight: number; description: string }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }

export interface HealthWorker { last_heartbeat_at: string | null; stale: boolean }
export interface Health {
  status: "ok" | "degraded";
  db: "ok" | "down";
  worker: HealthWorker;
}

export interface KbStats {
  updated_at?: string;
  document_count?: number;
  chunk_count?: number;
  entity_count?: number;
  relationship_count?: number;
  community_count?: number;
  community_report_count?: number;
  text_unit_count?: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  method?: string | null;
  rewritten_query?: string | null;
  rewrite_fell_back?: boolean;
  sources?: SourceRef[];
  prompt_tokens?: number | null;
  output_tokens?: number | null;
  elapsed_ms?: number | null;
  error?: string | null;
  cypher?: string | null;
  truncated?: boolean;
}

export interface Conversation {
  id: number;
  kb_id: number;
  title: string;
  updated_at?: string | null;
  snippet?: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface QueryParams {
  community_level?: number;
  response_type?: string;
  top_k?: number;
  temperature?: number;
  system_prompt?: string;
  hops?: number;
  cypher_timeout_ms?: number;
}

export interface QueryPreset {
  id: number;
  name: string;
  description: string;
  method: string;
  community_level?: number | null;
  response_type?: string | null;
  top_k?: number | null;
  temperature?: number | null;
  system_prompt?: string | null;
  hops?: number | null;
  is_builtin: boolean;
}
