/** KB config form state, defaults, and settings-serializer (replaces hand-written JSON). */

export interface LlmFields { provider: string; model: string; apiBase: string; apiKeyEnv: string; apiKey: string; apiVersion: string }
export interface EmbeddingFields extends LlmFields { enabled: boolean }

export interface KbFormState {
  method: string;
  minRatio: string;
  llm: LlmFields;
  embedding: EmbeddingFields;
  chunking: { size: number; overlap: number; encodingModel: string };
  extractGraph: { entityTypes: string; maxGleanings: number };
  summarize: { maxLength: number; maxInputTokens: number };
  communityReports: { structuredOutput: boolean; maxLength: number };
  cluster: { maxClusterSize: number };
  prompts: { extract: string; summarize: string; communityReport: string };
  advancedOverride: string;
}

const EMPTY_LLM: LlmFields = { provider: "", model: "", apiBase: "", apiKeyEnv: "", apiKey: "", apiVersion: "" };

export const DEFAULTS: KbFormState = {
  method: "standard",
  minRatio: "1.0",
  llm: { ...EMPTY_LLM },
  embedding: { ...EMPTY_LLM, enabled: false },
  chunking: { size: 1200, overlap: 100, encodingModel: "cl100k_base" },
  extractGraph: { entityTypes: "", maxGleanings: 0 },
  summarize: { maxLength: 500, maxInputTokens: 32000 },
  communityReports: { structuredOutput: true, maxLength: 2000 },
  cluster: { maxClusterSize: 10 },
  prompts: { extract: "", summarize: "", communityReport: "" },
  advancedOverride: "",
};

const LLM_MAP: [keyof LlmFields, string][] = [
  ["provider", "model_provider"], ["model", "model"], ["apiBase", "api_base"],
  ["apiKeyEnv", "api_key_env"], ["apiKey", "api_key"], ["apiVersion", "api_version"],
];

function pickLlm(f: LlmFields): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, snake] of LLM_MAP) {
    const v = (f as unknown as Record<string, string>)[k as string];
    if (v && v.trim()) out[snake] = v.trim();
  }
  return out;
}

export function buildSettings(state: KbFormState): Record<string, unknown> {
  const override = state.advancedOverride.trim();
  if (override) return JSON.parse(override) as Record<string, unknown>;

  const out: Record<string, unknown> = {};
  const llm = pickLlm(state.llm);
  if (Object.keys(llm).length) out.llm = llm;
  if (state.embedding.enabled) {
    const emb = pickLlm(state.embedding);
    if (Object.keys(emb).length) out.embedding = emb;
  }

  const addIf = <T>(key: string, val: T, def: T, bucket: string) => {
    if (val !== def) {
      const b = (out[bucket] ?? {}) as Record<string, unknown>;
      b[key] = val;
      out[bucket] = b;
    }
  };

  addIf("size", state.chunking.size, DEFAULTS.chunking.size, "chunking");
  addIf("overlap", state.chunking.overlap, DEFAULTS.chunking.overlap, "chunking");
  addIf("encoding_model", state.chunking.encodingModel, DEFAULTS.chunking.encodingModel, "chunking");

  addIf("max_cluster_size", state.cluster.maxClusterSize, DEFAULTS.cluster.maxClusterSize, "cluster_graph");

  addIf("max_gleanings", state.extractGraph.maxGleanings, DEFAULTS.extractGraph.maxGleanings, "extract_graph");
  const et = state.extractGraph.entityTypes.split(",").map((t) => t.trim()).filter(Boolean);
  if (et.length) {
    const b = (out.extract_graph ?? {}) as Record<string, unknown>;
    b.entity_types = et;
    out.extract_graph = b;
  }

  addIf("max_length", state.summarize.maxLength, DEFAULTS.summarize.maxLength, "summarize_descriptions");
  addIf("max_input_tokens", state.summarize.maxInputTokens, DEFAULTS.summarize.maxInputTokens, "summarize_descriptions");

  addIf("structured_output", state.communityReports.structuredOutput, DEFAULTS.communityReports.structuredOutput, "community_reports");
  addIf("max_length", state.communityReports.maxLength, DEFAULTS.communityReports.maxLength, "community_reports");

  if (state.prompts.extract.trim()) {
    const b = (out.extract_graph ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.extract.trim();
    out.extract_graph = b;
  }
  if (state.prompts.summarize.trim()) {
    const b = (out.summarize_descriptions ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.summarize.trim();
    out.summarize_descriptions = b;
  }
  if (state.prompts.communityReport.trim()) {
    const b = (out.community_reports ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.communityReport.trim();
    out.community_reports = b;
  }

  return out;
}

export function parseSettings(settings: Record<string, unknown>, method: string, minRatio: string): KbFormState {
  const f = (b: unknown, k: string, d: string) => String(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const n = (b: unknown, k: string, d: number) => Number(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const llm = (settings.llm as Record<string, unknown> | undefined) ?? {};
  const emb = (settings.embedding as Record<string, unknown> | undefined) ?? {};
  const ch = (settings.chunking as Record<string, unknown> | undefined) ?? {};
  const eg = (settings.extract_graph as Record<string, unknown> | undefined) ?? {};
  const su = (settings.summarize_descriptions as Record<string, unknown> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, unknown> | undefined) ?? {};
  const cl = (settings.cluster_graph as Record<string, unknown> | undefined) ?? {};
  const etRaw = eg.entity_types;
  const et = Array.isArray(etRaw) ? etRaw.join(", ") : typeof etRaw === "string" ? etRaw : "";
  return {
    ...DEFAULTS,
    method,
    minRatio,
    llm: {
      ...DEFAULTS.llm,
      provider: f(llm, "model_provider", ""),
      model: f(llm, "model", ""),
      apiBase: f(llm, "api_base", ""),
      apiKeyEnv: f(llm, "api_key_env", ""),
      apiKey: "",
      apiVersion: f(llm, "api_version", ""),
    },
    embedding: {
      ...DEFAULTS.embedding,
      enabled: !!settings.embedding,
      provider: f(emb, "model_provider", ""),
      model: f(emb, "model", ""),
      apiBase: f(emb, "api_base", ""),
      apiKeyEnv: f(emb, "api_key_env", ""),
      apiKey: "",
      apiVersion: f(emb, "api_version", ""),
    },
    chunking: {
      size: n(ch, "size", DEFAULTS.chunking.size),
      overlap: n(ch, "overlap", DEFAULTS.chunking.overlap),
      encodingModel: f(ch, "encoding_model", DEFAULTS.chunking.encodingModel),
    },
    extractGraph: {
      entityTypes: et,
      maxGleanings: n(eg, "max_gleanings", DEFAULTS.extractGraph.maxGleanings),
    },
    summarize: {
      maxLength: n(su, "max_length", DEFAULTS.summarize.maxLength),
      maxInputTokens: n(su, "max_input_tokens", DEFAULTS.summarize.maxInputTokens),
    },
    communityReports: {
      structuredOutput: (cr.structured_output ?? DEFAULTS.communityReports.structuredOutput) as boolean,
      maxLength: n(cr, "max_length", DEFAULTS.communityReports.maxLength),
    },
    cluster: {
      maxClusterSize: n(cl, "max_cluster_size", DEFAULTS.cluster.maxClusterSize),
    },
    prompts: {
      extract: f(eg, "prompt", ""),
      summarize: f(su, "prompt", ""),
      communityReport: f(cr, "prompt", ""),
    },
    advancedOverride: "",
  };
}
