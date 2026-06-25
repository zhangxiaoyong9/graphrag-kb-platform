export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type StepStatus = "pending" | "running" | "succeeded" | "partially_failed" | "failed";
export type UnitStatus = "pending" | "running" | "succeeded" | "failed";

export interface KbOut { id: number; name: string; method: string }
export interface DocumentOut { id: number; title: string; status: string | null }
export interface UnitProgress { pending: number; running: number; succeeded: number; failed: number; total: number }
export interface StepOut { id: number; name: string; ordinal: number; kind: string; status: StepStatus; progress: UnitProgress | null }
export interface JobOut { id: number; status: JobStatus; steps: StepOut[] }
export interface UnitOut { id: number; subject_id: string; status: UnitStatus; error: string | null; llm_raw_output: string | null; needs_reconsolidation: boolean }
export interface KbCreate { name: string; method?: string; settings_yaml?: string; min_unit_success_ratio?: number }
export interface DocumentCreate { title: string; text: string }
