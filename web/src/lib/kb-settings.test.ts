import { describe, expect, it } from "vitest";
import { buildSettings, parseSettings, DEFAULTS, type KbFormState } from "./kb-settings";

const base: KbFormState = {
  ...DEFAULTS,
  chunking: { ...DEFAULTS.chunking },
  extractGraph: { ...DEFAULTS.extractGraph },
  summarize: { ...DEFAULTS.summarize },
  communityReports: { ...DEFAULTS.communityReports },
  cluster: { ...DEFAULTS.cluster },
  prompts: { ...DEFAULTS.prompts },
  queryPrompts: { ...DEFAULTS.queryPrompts },
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

  it("never emits llm/embedding (providers live in profiles, not KB content)", () => {
    const out = buildSettings(base);
    expect(out.llm).toBeUndefined();
    expect(out.embedding).toBeUndefined();
    // community_reports holds only max_length; at default it is omitted entirely
    expect(out.community_reports).toBeUndefined();
  });

  it("emits community_reports.max_length when non-default", () => {
    const s = { ...base, communityReports: { maxLength: 1500 } };
    expect(buildSettings(s)).toEqual({ community_reports: { max_length: 1500 } });
  });

  it("emits concurrency when non-default", () => {
    const s = { ...base, concurrency: 8 };
    expect(buildSettings(s)).toEqual({ concurrency: 8 });
  });

  it("advanced override replaces everything", () => {
    const s = { ...base, advancedOverride: '{"chunking":{"size":200}}' };
    expect(buildSettings(s)).toEqual({ chunking: { size: 200 } });
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
  it("round-trips chunking + prompt", () => {
    const s = parseSettings(
      {
        chunking: { size: 300 },
        extract_graph: { prompt: "MY-PROMPT" },
      },
      "fast",
      "0.8",
    );
    expect(s.method).toBe("fast");
    expect(s.minRatio).toBe("0.8");
    expect(s.chunking.size).toBe(300);
    expect(s.prompts.extract).toBe("MY-PROMPT");
    // defaults for absent
    expect(s.cluster.maxClusterSize).toBe(10);
  });

  it("entity_types list -> csv", () => {
    const s = parseSettings(
      { extract_graph: { entity_types: ["ORG", "PERSON"] } },
      "standard",
      "1.0",
    );
    expect(s.extractGraph.entityTypes).toBe("ORG, PERSON");
  });

  it("ignores llm/embedding/structured_output (not KB content)", () => {
    const s = parseSettings(
      { llm: { model: "x" }, embedding: { model: "y" }, community_reports: { structured_output: false } },
      "standard",
      "1.0",
    );
    expect(s.communityReports.maxLength).toBe(DEFAULTS.communityReports.maxLength);
  });
});
