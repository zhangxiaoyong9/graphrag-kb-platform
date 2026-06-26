import { useEffect, useState } from "react";
import { createKb, getPromptDefaults, updateKb, type PromptDefaults } from "../api/client";
import type { KbOut } from "../api/types";
import { Button, Field } from "./ui";
import { IconPlus } from "./icons";
import {
  DEFAULTS,
  buildSettings,
  parseSettings,
  type KbFormState,
} from "../lib/kb-settings";

/** Sectioned, structured KB config form. Builds settings_yaml from fields so
 * users never hand-write JSON. The 高级 panel exposes a read-only preview and
 * an optional raw-settings override textarea (non-empty replaces form output). */
export default function KbForm({
  onCreated,
  kb,
  onSaved,
}: {
  onCreated?: (kb: KbOut) => void;
  kb?: KbOut;
  onSaved?: () => void;
}) {
  const isEdit = !!kb;
  const [s, setS] = useState<KbFormState>(() =>
    isEdit
      ? parseSettings(
          (kb!.settings ?? {}) as Record<string, unknown>,
          kb!.method,
          "1.0",
        )
      : {
          ...DEFAULTS,
          llm: { ...DEFAULTS.llm },
          embedding: { ...DEFAULTS.embedding },
          chunking: { ...DEFAULTS.chunking },
          extractGraph: { ...DEFAULTS.extractGraph },
          summarize: { ...DEFAULTS.summarize },
          communityReports: { ...DEFAULTS.communityReports },
          cluster: { ...DEFAULTS.cluster },
          advancedOverride: "",
        },
  );
  const [name, setName] = useState(kb?.name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
  const [defaultsError, setDefaultsError] = useState(false);
  const [showDef, setShowDef] = useState<Record<string, boolean>>({
    extract: false, summarize: false, report: false,
    q_localSystem: false, q_globalMap: false, q_globalReduce: false, q_basicSystem: false,
  });

  useEffect(() => {
    let cancelled = false;
    setDefaultsError(false);
    getPromptDefaults()
      .then((d) => {
        if (!cancelled) setDefaults(d);
      })
      .catch(() => {
        if (!cancelled) setDefaultsError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const set = <K extends keyof KbFormState>(k: K, v: KbFormState[K]) =>
    setS((p) => ({ ...p, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const settingsObj = buildSettings(s); // throws on bad advancedOverride
      const settings_yaml = JSON.stringify(settingsObj);
      if (isEdit && kb) {
        await updateKb(kb.id, { name, method: s.method, settings_yaml });
        onSaved?.();
      } else {
        const created = await createKb({
          name,
          method: s.method,
          settings_yaml,
          min_unit_success_ratio: parseFloat(s.minRatio),
        });
        onCreated?.(created);
        setName("");
      }
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
        <Field label="并发数" hint="unit 级并发（默认 4）">
          <input
            className="input"
            type="number"
            min="1"
            max="32"
            value={s.concurrency}
            onChange={(e) => set("concurrency", Math.max(1, Number(e.target.value)))}
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
          <Field label="api_key_envs" hint="多 key 负载均衡（逗号分隔环境变量名）">
            <input
              className="input"
              value={s.llm.apiKeyEnvs}
              placeholder="DEEPSEEK_API_KEY_1, DEEPSEEK_API_KEY_2"
              onChange={(e) => set("llm", { ...s.llm, apiKeyEnvs: e.target.value })}
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

      {/* 提示词 Prompts */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          提示词 Prompts（留空=用 graphrag 默认）
        </summary>
        <div className="mt-3 space-y-4">
          {/* 抽取 prompt */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[13px] font-medium text-body">
                图谱抽取 extract_graph prompt
              </span>
              <button
                type="button"
                className="text-[12px] text-brand hover:underline"
                onClick={() =>
                  setShowDef((d) => ({ ...d, extract: !d.extract }))
                }
              >
                {showDef.extract ? "隐藏默认" : "查看 graphrag 默认"}
              </button>
            </div>
            <textarea
              className="textarea h-28 font-mono text-[12px]"
              value={s.prompts.extract}
              onChange={(e) =>
                set("prompts", { ...s.prompts, extract: e.target.value })
              }
              placeholder="留空使用 graphrag 默认；可粘贴 prompt-tune 产出或自定义中文 prompt"
            />
            {showDef.extract && (
              <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">
                {defaults?.extract_graph ??
                  (defaultsError ? "加载默认失败" : "加载默认中…")}
              </pre>
            )}
          </div>
          {/* 摘要 summarize_descriptions prompt */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[13px] font-medium text-body">
                描述摘要 summarize_descriptions prompt
              </span>
              <button
                type="button"
                className="text-[12px] text-brand hover:underline"
                onClick={() =>
                  setShowDef((d) => ({ ...d, summarize: !d.summarize }))
                }
              >
                {showDef.summarize ? "隐藏默认" : "查看 graphrag 默认"}
              </button>
            </div>
            <textarea
              className="textarea h-28 font-mono text-[12px]"
              value={s.prompts.summarize}
              onChange={(e) =>
                set("prompts", { ...s.prompts, summarize: e.target.value })
              }
              placeholder="留空使用 graphrag 默认"
            />
            {showDef.summarize && (
              <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">
                {defaults?.summarize_descriptions ??
                  (defaultsError ? "加载默认失败" : "加载默认中…")}
              </pre>
            )}
          </div>
          {/* 社区报告 community_reports prompt */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[13px] font-medium text-body">
                社区报告 community_reports prompt
              </span>
              <button
                type="button"
                className="text-[12px] text-brand hover:underline"
                onClick={() =>
                  setShowDef((d) => ({ ...d, report: !d.report }))
                }
              >
                {showDef.report ? "隐藏默认" : "查看 graphrag 默认"}
              </button>
            </div>
            <textarea
              className="textarea h-28 font-mono text-[12px]"
              value={s.prompts.communityReport}
              onChange={(e) =>
                set("prompts", {
                  ...s.prompts,
                  communityReport: e.target.value,
                })
              }
              placeholder="留空使用 graphrag 默认"
            />
            {showDef.report && (
              <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">
                {defaults?.community_reports ??
                  (defaultsError ? "加载默认失败" : "加载默认中…")}
              </pre>
            )}
          </div>
        </div>

        {/* 查询 Prompt */}
        <p className="px-1 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted">
          查询 Prompt（留空=用 graphrag 默认）
        </p>
        {([
          ["localSystem", "local 检索 prompt", "local_system"],
          ["globalMap", "global map prompt", "global_map"],
          ["globalReduce", "global reduce prompt", "global_reduce"],
          ["basicSystem", "basic 检索 prompt", "basic_system"],
        ] as const).map(([field, label, defaultKey]) => (
          <div key={field} className="mt-3">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[13px] font-medium text-body">{label}</span>
              <button
                type="button"
                className="text-[12px] text-brand hover:underline"
                onClick={() =>
                  setShowDef((d) => ({
                    ...d,
                    [`q_${field}`]: !d[`q_${field}`],
                  }))
                }
              >
                {showDef[`q_${field}`] ? "隐藏默认" : "查看 graphrag 默认"}
              </button>
            </div>
            <textarea
              className="textarea h-28 font-mono text-[12px]"
              value={s.queryPrompts[field]}
              onChange={(e) =>
                set("queryPrompts", { ...s.queryPrompts, [field]: e.target.value })
              }
              placeholder="留空使用 graphrag 默认"
            />
            {showDef[`q_${field}`] && (
              <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">
                {defaults?.[defaultKey] ??
                  (defaultsError ? "加载默认失败" : "加载默认中…")}
              </pre>
            )}
          </div>
        ))}
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

      {error && (
        <p className="text-[13px] text-danger">{isEdit ? "保存失败" : "创建失败"}：{error}</p>
      )}
      <Button type="submit" variant="primary" disabled={busy} className="w-full">
        <IconPlus width={16} height={16} />
        {busy ? (isEdit ? "保存中…" : "创建中…") : isEdit ? "保存修改" : "创建知识库"}
      </Button>
    </form>
  );
}
