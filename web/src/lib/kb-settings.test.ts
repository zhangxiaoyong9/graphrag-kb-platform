import { describe, expect, it } from "vitest";
import { buildSettings, DEFAULTS, type KbFormState } from "./kb-settings";

const base: KbFormState = {
  ...DEFAULTS,
  llm: { ...DEFAULTS.llm },
  embedding: { ...DEFAULTS.embedding },
  chunking: { ...DEFAULTS.chunking },
  extractGraph: { ...DEFAULTS.extractGraph },
  summarize: { ...DEFAULTS.summarize },
  communityReports: { ...DEFAULTS.communityReports },
  cluster: { ...DEFAULTS.cluster },
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
    const s = { ...base, embedding: { enabled: true, provider: "ollama", model: "nomic-embed-text", apiBase: "http://localhost:11434", apiKey: "ollama", apiKeyEnv: "", apiVersion: "" } };
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
});
