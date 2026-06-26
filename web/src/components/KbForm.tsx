import { useState } from "react";
import { createKb } from "../api/client";
import type { KbOut } from "../api/types";
import { Button, Field } from "./ui";
import { IconPlus } from "./icons";
import { DEFAULTS, buildSettings, type KbFormState } from "../lib/kb-settings";

/** Sectioned, structured KB config form. Builds settings_yaml from fields so
 * users never hand-write JSON. The 高级 panel exposes a read-only preview and
 * an optional raw-settings override textarea (non-empty replaces form output). */
export default function KbForm({ onCreated }: { onCreated: (kb: KbOut) => void }) {
  const [s, setS] = useState<KbFormState>(() => ({
    ...DEFAULTS,
    llm: { ...DEFAULTS.llm },
    embedding: { ...DEFAULTS.embedding },
    chunking: { ...DEFAULTS.chunking },
    extractGraph: { ...DEFAULTS.extractGraph },
    summarize: { ...DEFAULTS.summarize },
    communityReports: { ...DEFAULTS.communityReports },
    cluster: { ...DEFAULTS.cluster },
    advancedOverride: "",
  }));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const set = <K extends keyof KbFormState>(k: K, v: KbFormState[K]) =>
    setS((p) => ({ ...p, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const settingsObj = buildSettings(s); // throws on bad advancedOverride
      const kb = await createKb({
        name,
        method: s.method,
        settings_yaml: JSON.stringify(settingsObj),
        min_unit_success_ratio: parseFloat(s.minRatio),
      });
      onCreated(kb);
      setName("");
    } catch (err) {
      setError(String((err as Error).message ?? err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {/* 基础 */}
      <Field label="知识库名称">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="请输入知识库名称"
          required
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="索引方法" hint="standard / fast">
          <select
            className="select"
            value={s.method}
            onChange={(e) => set("method", e.target.value)}
          >
            <option value="standard">standard（LLM 精抽取）</option>
            <option value="fast">fast（NLP 快速）</option>
          </select>
        </Field>
        <Field label="最小成功率" hint="低于此值步骤失败">
          <input
            className="input"
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={s.minRatio}
            onChange={(e) => set("minRatio", e.target.value)}
          />
        </Field>
      </div>

      {/* LLM 模型 */}
      <details open>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          LLM 模型
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="provider" hint="deepseek / openai / ollama">
            <input
              className="input"
              value={s.llm.provider}
              placeholder="deepseek"
              onChange={(e) => set("llm", { ...s.llm, provider: e.target.value })}
            />
          </Field>
          <Field label="model">
            <input
              className="input"
              value={s.llm.model}
              placeholder="deepseek-chat"
              onChange={(e) => set("llm", { ...s.llm, model: e.target.value })}
            />
          </Field>
          <Field label="api_base" hint="自定义端点（可选）">
            <input
              className="input"
              value={s.llm.apiBase}
              placeholder="https://api.deepseek.com"
              onChange={(e) => set("llm", { ...s.llm, apiBase: e.target.value })}
            />
          </Field>
          <Field label="api_key_env" hint="密钥环境变量名（推荐）">
            <input
              className="input"
              value={s.llm.apiKeyEnv}
              placeholder="DEEPSEEK_API_KEY"
              onChange={(e) => set("llm", { ...s.llm, apiKeyEnv: e.target.value })}
            />
          </Field>
          <Field label="api_key" hint="明文（不推荐，会入库）">
            <input
              className="input"
              value={s.llm.apiKey}
              onChange={(e) => set("llm", { ...s.llm, apiKey: e.target.value })}
            />
          </Field>
          <Field label="api_version" hint="仅 Azure">
            <input
              className="input"
              value={s.llm.apiVersion}
              onChange={(e) => set("llm", { ...s.llm, apiVersion: e.target.value })}
            />
          </Field>
        </div>
      </details>

      {/* Embedding 模型 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          Embedding 模型
        </summary>
        <div className="mt-3 space-y-3">
          <label className="flex items-center gap-2 text-[13px]">
            <input
              type="checkbox"
              checked={s.embedding.enabled}
              onChange={(e) =>
                set("embedding", { ...s.embedding, enabled: e.target.checked })
              }
            />{" "}
            启用嵌入（local/basic/drift 需要）
          </label>
          {s.embedding.enabled && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="provider">
                <input
                  className="input"
                  value={s.embedding.provider}
                  placeholder="ollama"
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, provider: e.target.value })
                  }
                />
              </Field>
              <Field label="model">
                <input
                  className="input"
                  value={s.embedding.model}
                  placeholder="nomic-embed-text"
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, model: e.target.value })
                  }
                />
              </Field>
              <Field label="api_base">
                <input
                  className="input"
                  value={s.embedding.apiBase}
                  placeholder="http://localhost:11434"
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, apiBase: e.target.value })
                  }
                />
              </Field>
              <Field label="api_key_env">
                <input
                  className="input"
                  value={s.embedding.apiKeyEnv}
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, apiKeyEnv: e.target.value })
                  }
                />
              </Field>
              <Field label="api_key">
                <input
                  className="input"
                  value={s.embedding.apiKey}
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, apiKey: e.target.value })
                  }
                />
              </Field>
              <Field label="api_version">
                <input
                  className="input"
                  value={s.embedding.apiVersion}
                  onChange={(e) =>
                    set("embedding", { ...s.embedding, apiVersion: e.target.value })
                  }
                />
              </Field>
            </div>
          )}
        </div>
      </details>

      {/* 分块 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          分块 Chunking
        </summary>
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="size">
            <input
              className="input"
              type="number"
              value={s.chunking.size}
              onChange={(e) =>
                set("chunking", { ...s.chunking, size: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="overlap">
            <input
              className="input"
              type="number"
              value={s.chunking.overlap}
              onChange={(e) =>
                set("chunking", { ...s.chunking, overlap: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="encoding_model">
            <input
              className="input"
              value={s.chunking.encodingModel}
              onChange={(e) =>
                set("chunking", {
                  ...s.chunking,
                  encodingModel: e.target.value,
                })
              }
            />
          </Field>
        </div>
      </details>

      {/* 图谱抽取 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          图谱抽取 Extract Graph
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="entity_types" hint="逗号分隔">
            <input
              className="input"
              value={s.extractGraph.entityTypes}
              placeholder="organization,person,geo"
              onChange={(e) =>
                set("extractGraph", {
                  ...s.extractGraph,
                  entityTypes: e.target.value,
                })
              }
            />
          </Field>
          <Field label="max_gleanings">
            <input
              className="input"
              type="number"
              value={s.extractGraph.maxGleanings}
              onChange={(e) =>
                set("extractGraph", {
                  ...s.extractGraph,
                  maxGleanings: Number(e.target.value),
                })
              }
            />
          </Field>
        </div>
      </details>

      {/* 描述摘要 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          描述摘要 Summarize
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="max_length">
            <input
              className="input"
              type="number"
              value={s.summarize.maxLength}
              onChange={(e) =>
                set("summarize", {
                  ...s.summarize,
                  maxLength: Number(e.target.value),
                })
              }
            />
          </Field>
          <Field label="max_input_tokens">
            <input
              className="input"
              type="number"
              value={s.summarize.maxInputTokens}
              onChange={(e) =>
                set("summarize", {
                  ...s.summarize,
                  maxInputTokens: Number(e.target.value),
                })
              }
            />
          </Field>
        </div>
      </details>

      {/* 社区报告 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          社区报告 Community Reports
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="structured_output" hint="DeepSeek 关闭">
            <label className="flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={s.communityReports.structuredOutput}
                onChange={(e) =>
                  set("communityReports", {
                    ...s.communityReports,
                    structuredOutput: e.target.checked,
                  })
                }
              />{" "}
              结构化输出（json_schema）
            </label>
          </Field>
          <Field label="max_length">
            <input
              className="input"
              type="number"
              value={s.communityReports.maxLength}
              onChange={(e) =>
                set("communityReports", {
                  ...s.communityReports,
                  maxLength: Number(e.target.value),
                })
              }
            />
          </Field>
        </div>
      </details>

      {/* 聚类 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          聚类 Clustering
        </summary>
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="max_cluster_size">
            <input
              className="input"
              type="number"
              value={s.cluster.maxClusterSize}
              onChange={(e) =>
                set("cluster", {
                  ...s.cluster,
                  maxClusterSize: Number(e.target.value),
                })
              }
            />
          </Field>
        </div>
      </details>

      {/* 高级：只读预览 + 覆盖框 */}
      <div>
        <button
          type="button"
          className="text-[13px] text-brand hover:underline"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? "隐藏高级" : "高级（原始 settings_yaml 覆盖）"}
        </button>
        {showAdvanced && (
          <div className="mt-3 space-y-2">
            <pre className="rounded-lg bg-surface-2 p-3 text-[11px] text-muted overflow-x-auto">
              {(() => {
                try {
                  return JSON.stringify(buildSettings(s), null, 2);
                } catch {
                  return "（高级覆盖框 JSON 无效，无法预览）";
                }
              })()}
            </pre>
            <Field label="原始 settings_yaml（非空则覆盖表单）">
              <textarea
                className="textarea h-24 font-mono text-[12px]"
                value={s.advancedOverride}
                onChange={(e) => set("advancedOverride", e.target.value)}
                placeholder='{"llm":{"model_provider":"..."}}'
              />
            </Field>
          </div>
        )}
      </div>

      {error && <p className="text-[13px] text-danger">创建失败：{error}</p>}
      <Button type="submit" variant="primary" disabled={busy} className="w-full">
        <IconPlus width={16} height={16} />
        {busy ? "创建中…" : "创建知识库"}
      </Button>
    </form>
  );
}
