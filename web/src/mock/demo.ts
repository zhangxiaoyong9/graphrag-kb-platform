/**
 * Centralized demo / sample data.
 *
 * The app talks to the real FastAPI backend in normal use; this module is the
 * SINGLE home for any illustrative data, used only by the `/demo` preview page
 * (so the UI can be screenshotted/reviewed without running the Python worker).
 * Never scatter sample objects inside feature components — add them here.
 */
import type {
  KbOut,
  DocumentOut,
  JobOut,
  UnitOut,
  KbCost,
  JobCost,
  GraphData,
  QueryResult,
} from "../api/types";

export const demoKbs: KbOut[] = [
  { id: 1, name: "产品研报知识库", method: "standard" },
  { id: 2, name: "客服问答语料", method: "fast" },
  { id: 3, name: "技术文档库", method: "standard" },
];

export const demoDocs: DocumentOut[] = [
  { id: 11, title: "2025 新能源汽车行业深度报告.pdf", status: "ready", bytes: 2_482_944, chunk_count: 184 },
  { id: 12, title: "锂电产业链梳理.md", status: "ready", bytes: 542_720, chunk_count: 41 },
  { id: 13, title: "会议纪要_Q1.md", status: "ready", bytes: 96_256, chunk_count: 8 },
  { id: 14, title: "待处理.txt", status: "uploaded", bytes: 12_288, chunk_count: 0 },
];

export const demoSteps: JobOut["steps"] = [
  { id: 101, name: "chunk_documents", ordinal: 1, kind: "atomic", status: "succeeded", progress: { pending: 0, running: 0, succeeded: 233, failed: 0, total: 233 } },
  { id: 102, name: "extract_graph", ordinal: 2, kind: "unit_fanout", status: "succeeded", progress: { pending: 0, running: 0, succeeded: 230, failed: 3, total: 233 } },
  { id: 103, name: "summarize_descriptions", ordinal: 3, kind: "unit_fanout", status: "partially_failed", progress: { pending: 0, running: 0, succeeded: 41, failed: 2, total: 43 } },
  { id: 104, name: "finalize_graph", ordinal: 4, kind: "atomic", status: "succeeded", progress: { pending: 0, running: 0, succeeded: 1, failed: 0, total: 1 } },
  { id: 105, name: "create_communities", ordinal: 5, kind: "atomic", status: "running", progress: { pending: 0, running: 1, succeeded: 0, failed: 0, total: 1 } },
  { id: 106, name: "community_reports", ordinal: 6, kind: "unit_fanout", status: "pending", progress: { pending: 12, running: 0, succeeded: 0, failed: 0, total: 12 } },
  { id: 107, name: "generate_text_embeddings", ordinal: 7, kind: "atomic", status: "pending", progress: null },
];

export const demoJob: JobOut = { id: 7, status: "running", steps: demoSteps };

export const demoUnits: UnitOut[] = [
  { id: 911, subject_id: "chunk-0042-nvidia", status: "succeeded", error: null, llm_raw_output: null, needs_reconsolidation: false },
  { id: 912, subject_id: "chunk-0043-tesla", status: "succeeded", error: null, llm_raw_output: null, needs_reconsolidation: false },
  { id: 913, subject_id: "chunk-0044-byd", status: "failed", error: "LiteLLM RateLimitError: 429 Too Many Requests (retry budget exhausted).", llm_raw_output: null, needs_reconsolidation: false },
  { id: 914, subject_id: "chunk-0045-catl", status: "pending", error: null, llm_raw_output: null, needs_reconsolidation: false },
  { id: 915, subject_id: "chunk-0046-lg", status: "running", error: null, llm_raw_output: null, needs_reconsolidation: false },
];

export const demoJobCost: JobCost = {
  total_usd: 0.1842,
  by_step: { extract_graph: 0.0921, summarize_descriptions: 0.0413, community_reports: 0.0508 },
  by_model: {
    "deepseek-chat": { model: "deepseek-chat", prompt_tokens: 184320, completion_tokens: 41210, usd: 0.1842 },
  },
};

export const demoKbCost: KbCost = {
  total_usd: 1.2764,
  by_step: { extract_graph: 0.6102, summarize_descriptions: 0.2901, community_reports: 0.3201, generate_text_embeddings: 0.056 },
  by_model: {
    "deepseek-chat": { model: "deepseek-chat", prompt_tokens: 1_204_330, completion_tokens: 268_410, usd: 1.2204 },
    "text-embedding-3-small": { model: "text-embedding-3-small", prompt_tokens: 980_120, completion_tokens: 0, usd: 0.056 },
  },
  by_job: { "7": 0.1842, "6": 0.7101, "5": 0.3821 },
};

export const demoGraph: GraphData = {
  nodes: [
    { id: "宁德时代", title: "宁德时代", type: "organization", degree: 14, community: "c1" },
    { id: "比亚迪", title: "比亚迪", type: "organization", degree: 12, community: "c1" },
    { id: "特斯拉", title: "特斯拉", type: "organization", degree: 10, community: "c2" },
    { id: "锂电池", title: "锂电池", type: "concept", degree: 9, community: "c1" },
    { id: "蔚来", title: "蔚来", type: "organization", degree: 6, community: "c2" },
    { id: "理想汽车", title: "理想汽车", type: "organization", degree: 5, community: "c2" },
    { id: "磷酸铁锂", title: "磷酸铁锂", type: "concept", degree: 4, community: "c1" },
    { id: "充电桩", title: "充电桩", type: "concept", degree: 3, community: "c3" },
  ],
  edges: [
    { source: "宁德时代", target: "锂电池", weight: 6, description: "生产" },
    { source: "比亚迪", target: "锂电池", weight: 5, description: "自研" },
    { source: "宁德时代", target: "磷酸铁锂", weight: 4, description: "核心技术" },
    { source: "特斯拉", target: "宁德时代", weight: 3, description: "供应商" },
    { source: "特斯拉", target: "蔚来", weight: 2, description: "竞争对手" },
    { source: "蔚来", target: "理想汽车", weight: 2, description: "竞争对手" },
    { source: "比亚迪", target: "磷酸铁锂", weight: 3, description: "应用" },
    { source: "蔚来", target: "充电桩", weight: 2, description: "布局" },
  ],
};

export const demoQuery: QueryResult = {
  answer:
    "宁德时代是全球最大的动力电池制造商，与比亚迪、特斯拉等车企有密切的供应与合作关系，其核心竞争力集中在锂电池及磷酸铁锂技术上。",
  method: "global",
  error: null,
};

export const demoHealth = {
  status: "ok",
  db: "ok",
  worker: { last_heartbeat_at: new Date(Date.now() - 12_000).toISOString(), stale: false },
};
