/** KB content-only config form state, defaults, and settings-serializer.
 *
 * Provider connection + API keys live in provider profiles (managed on the
 * Provider 配置 page, encrypted at rest). A KB stores ONLY content/quality
 * params here; structured_output follows the selected LLM profile, not the KB. */

export interface KbFormState {
  method: string;
  minRatio: string;
  chunking: { size: number; overlap: number; encodingModel: string };
  extractGraph: { entityTypes: string; maxGleanings: number };
  summarize: { maxLength: number; maxInputTokens: number };
  communityReports: { maxLength: number };
  cluster: { maxClusterSize: number };
  prompts: { extract: string; summarize: string; communityReport: string };
  queryPrompts: {
    localSystem: string;
    globalMap: string;
    globalReduce: string;
    basicSystem: string;
  };
  concurrency: number;
  advancedOverride: string;
}

export const DEFAULTS: KbFormState = {
  method: "standard",
  minRatio: "1.0",
  chunking: { size: 1200, overlap: 100, encodingModel: "cl100k_base" },
  extractGraph: { entityTypes: "", maxGleanings: 0 },
  summarize: { maxLength: 500, maxInputTokens: 32000 },
  communityReports: { maxLength: 2000 },
  cluster: { maxClusterSize: 10 },
  prompts: { extract: "", summarize: "", communityReport: "" },
  queryPrompts: { localSystem: "", globalMap: "", globalReduce: "", basicSystem: "" },
  concurrency: 4,
  advancedOverride: "",
};

export function buildSettings(state: KbFormState): Record<string, unknown> {
  const override = state.advancedOverride.trim();
  if (override) return JSON.parse(override) as Record<string, unknown>;

  const out: Record<string, unknown> = {};

  const addIf = <T>(key: string, val: T, def: T, bucket: string) => {
    if (val !== def) {
      const b = (out[bucket] ?? {}) as Record<string, unknown>;
      b[key] = val;
      out[bucket] = b;
    }
  };

  addIf("size", state.chunking.size, DEFAULTS.chunking.size, "chunking");
  addIf("overlap", state.chunking.overlap, DEFAULTS.chunking.overlap, "chunking");
  addIf(
    "encoding_model",
    state.chunking.encodingModel,
    DEFAULTS.chunking.encodingModel,
    "chunking",
  );

  addIf(
    "max_cluster_size",
    state.cluster.maxClusterSize,
    DEFAULTS.cluster.maxClusterSize,
    "cluster_graph",
  );

  addIf(
    "max_gleanings",
    state.extractGraph.maxGleanings,
    DEFAULTS.extractGraph.maxGleanings,
    "extract_graph",
  );
  const et = state.extractGraph.entityTypes
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  if (et.length) {
    const b = (out.extract_graph ?? {}) as Record<string, unknown>;
    b.entity_types = et;
    out.extract_graph = b;
  }

  addIf(
    "max_length",
    state.summarize.maxLength,
    DEFAULTS.summarize.maxLength,
    "summarize_descriptions",
  );
  addIf(
    "max_input_tokens",
    state.summarize.maxInputTokens,
    DEFAULTS.summarize.maxInputTokens,
    "summarize_descriptions",
  );

  addIf(
    "max_length",
    state.communityReports.maxLength,
    DEFAULTS.communityReports.maxLength,
    "community_reports",
  );

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

  // query prompts
  const qp = state.queryPrompts;
  if (
    qp.localSystem.trim() ||
    qp.globalMap.trim() ||
    qp.globalReduce.trim() ||
    qp.basicSystem.trim()
  ) {
    const q: Record<string, string> = {};
    if (qp.localSystem.trim()) q.local_system = qp.localSystem.trim();
    if (qp.globalMap.trim()) q.global_map = qp.globalMap.trim();
    if (qp.globalReduce.trim()) q.global_reduce = qp.globalReduce.trim();
    if (qp.basicSystem.trim()) q.basic_system = qp.basicSystem.trim();
    out.query_prompts = q;
  }

  if (state.concurrency !== DEFAULTS.concurrency) out.concurrency = state.concurrency;

  return out;
}

export function parseSettings(
  settings: Record<string, unknown>,
  method: string,
  minRatio: string,
): KbFormState {
  const f = (b: unknown, k: string, d: string) =>
    String(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const n = (b: unknown, k: string, d: number) =>
    Number(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const ch = (settings.chunking as Record<string, unknown> | undefined) ?? {};
  const eg = (settings.extract_graph as Record<string, unknown> | undefined) ?? {};
  const su = (settings.summarize_descriptions as Record<string, unknown> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, unknown> | undefined) ?? {};
  const cl = (settings.cluster_graph as Record<string, unknown> | undefined) ?? {};
  const qpS = (settings.query_prompts as Record<string, unknown> | undefined) ?? {};
  const etRaw = eg.entity_types;
  const et = Array.isArray(etRaw)
    ? etRaw.join(", ")
    : typeof etRaw === "string"
      ? etRaw
      : "";
  return {
    ...DEFAULTS,
    method,
    minRatio,
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
    queryPrompts: {
      localSystem: f(qpS, "local_system", ""),
      globalMap: f(qpS, "global_map", ""),
      globalReduce: f(qpS, "global_reduce", ""),
      basicSystem: f(qpS, "basic_system", ""),
    },
    concurrency: Number(settings.concurrency ?? DEFAULTS.concurrency),
    advancedOverride: "",
  };
}
