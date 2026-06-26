import { describe, expect, it } from "vitest";
import { buildSettings, parseSettings, DEFAULTS, type KbFormState } from "./kb-settings";

const base: KbFormState = {
  ...DEFAULTS,
  llm: { ...DEFAULTS.llm },
  embedding: { ...DEFAULTS.embedding },
  chunking: { ...DEFAULTS.chunking },
  extractGraph: { ...DEFAULTS.extractGraph },
  summarize: { ...DEFAULTS.summarize },
  communityReports: { ...DEFAULTS.communityReports },
  cluster: { ...DEFAULTS.cluster },
  prompts: { ...DEFAULTS.prompts },
  advancedOverride: "",
};

describe("buildSettings", () => {
  it("all defaults -> empty object", () => {
    expect(buildSettings(base)).toEqual({});
  });

  it("emits only non-default chunking field", () => {
    const s = { ...base, chunking: { ...DEFAULTS.chunking, size: 300 } };
    expect(buildSettings(s)).toEqual({ chunking: { size: 300 } });
  });

  it("emits llm non-empty fields only", () => {
    const s = { ...base, llm: { ...DEFAULTS.llm, provider: "deepseek", model: "deepseek-chat", apiKeyEnv: "DEEPSEEK_API_KEY" } };
    expect(buildSettings(s)).toEqual({
      llm: { model_provider: "deepseek", model: "deepseek-chat", api_key_env: "DEEPSEEK_API_KEY" },
    });
  });

  it("omits embedding when disabled", () => {
    const s = { ...base, embedding: { ...DEFAULTS.embedding, enabled: false, provider: "ollama" } };
    expect(buildSettings(s)).toEqual({});
  });

  it("emits embedding when enabled + filled", () => {
    const s = { ...base, embedding: { enabled: true, provider: "ollama", model: "nomic-embed-text", apiBase: "http://localhost:11434", apiKey: "ollama", apiKeyEnv: "", apiKeyEnvs: "", apiVersion: "" } };
    expect(buildSettings(s)).toEqual({
      embedding: { model_provider: "ollama", model: "nomic-embed-text", api_base: "http://localhost:11434", api_key: "ollama" },
    });
  });

  it("emits community_reports.structured_output when false (default true)", () => {
    const s = { ...base, communityReports: { ...DEFAULTS.communityReports, structuredOutput: false } };
    expect(buildSettings(s)).toEqual({ community_reports: { structured_output: false } });
  });

  it("advanced override replaces everything", () => {
    const s = { ...base, advancedOverride: '{"llm":{"model":"x"}}' };
    expect(buildSettings(s)).toEqual({ llm: { model: "x" } });
  });

  it("advanced override invalid JSON throws", () => {
    const s = { ...base, advancedOverride: "{not json" };
    expect(() => buildSettings(s)).toThrow();
  });

  it("entity_types csv -> list", () => {
    const s = { ...base, extractGraph: { entityTypes: "ORG, PERSON", maxGleanings: 0 } };
    expect(buildSettings(s)).toEqual({ extract_graph: { entity_types: ["ORG", "PERSON"] } });
  });

  it("emits prompts only when non-empty", () => {
    const s = {
      ...base,
      prompts: { extract: "MY-EXTRACT", summarize: "", communityReport: "MY-REPORT" },
    };
    expect(buildSettings(s)).toEqual({
      extract_graph: { prompt: "MY-EXTRACT" },
      community_reports: { prompt: "MY-REPORT" },
    });
  });

  it("omits prompts when all empty", () => {
    expect(buildSettings({ ...base, prompts: { extract: "", summarize: "", communityReport: "" } })).toEqual({});
  });
});

describe("parseSettings", () => {
  it("parseSettings round-trips llm + chunking + prompt", () => {
    const s = parseSettings(
      {
        llm: { model_provider: "deepseek", model: "deepseek-chat", api_key_env: "DEEPSEEK_API_KEY" },
        chunking: { size: 300 },
        extract_graph: { prompt: "MY-PROMPT" },
      },
      "fast",
      "0.8",
    );
    expect(s.method).toBe("fast");
    expect(s.minRatio).toBe("0.8");
    expect(s.llm).toMatchObject({ provider: "deepseek", model: "deepseek-chat", apiKeyEnv: "DEEPSEEK_API_KEY" });
    expect(s.chunking.size).toBe(300);
    expect(s.prompts.extract).toBe("MY-PROMPT");
    // defaults for absent
    expect(s.cluster.maxClusterSize).toBe(10);
    expect(s.embedding.enabled).toBe(false);
  });

  it("parseSettings entity_types list -> csv + embedding enabled", () => {
    const s = parseSettings(
      { extract_graph: { entity_types: ["ORG", "PERSON"] }, embedding: { model_provider: "ollama", model: "nomic-embed-text" } },
      "standard",
      "1.0",
    );
    expect(s.extractGraph.entityTypes).toBe("ORG, PERSON");
    expect(s.embedding.enabled).toBe(true);
    expect(s.embedding.model).toBe("nomic-embed-text");
  });
});
