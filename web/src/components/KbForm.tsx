import { useEffect, useState } from "react";
import {
  createKb,
  getPromptDefaults,
  listProfiles,
  updateKb,
  type PromptDefaults,
} from "../api/client";
import type { KbDetail, KbOut, ProviderProfile } from "../api/types";
import { Button, Field } from "./ui";
import { IconPlus } from "./icons";
import {
  DEFAULTS,
  buildSettings,
  parseSettings,
  type KbFormState,
} from "../lib/kb-settings";

/** Sectioned KB config form. Provider connection + keys are picked from
 * reusable provider profiles (managed on the Provider 配置 page); this form
 * captures only the KB's name, method, and content/quality params, building
 * settings_yaml from structured fields so users never hand-write JSON.
 *
 * The 高级 panel exposes a read-only preview and an optional raw-settings
 * override textarea (non-empty replaces form output). */
export default function KbForm({
  onCreated,
  kb,
  onSaved,
}: {
  onCreated?: (kb: KbOut) => void;
  kb?: KbDetail;
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
          chunking: { ...DEFAULTS.chunking },
          extractGraph: { ...DEFAULTS.extractGraph },
          summarize: { ...DEFAULTS.summarize },
          communityReports: { ...DEFAULTS.communityReports },
          cluster: { ...DEFAULTS.cluster },
          prompts: { ...DEFAULTS.prompts },
          queryPrompts: { ...DEFAULTS.queryPrompts },
        },
  );
  const [name, setName] = useState(kb?.name ?? "");
  const [llmProfileId, setLlmProfileId] = useState<number | null>(
    kb?.llm_profile?.id ?? null,
  );
  const [embeddingProfileId, setEmbeddingProfileId] = useState<number | null>(
    kb?.embedding_profile?.id ?? null,
  );
  const [llmProfiles, setLlmProfiles] = useState<ProviderProfile[]>([]);
  const [embProfiles, setEmbProfiles] = useState<ProviderProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
  const [defaultsError, setDefaultsError] = useState(false);
  const [showDef, setShowDef] = useState<Record<string, boolean>>({
    extract: false,
    summarize: false,
    report: false,
    q_localSystem: false,
    q_globalMap: false,
    q_globalReduce: false,
    q_basicSystem: false,
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
    listProfiles("llm")
      .then(setLlmProfiles)
      .catch(() => {});
    listProfiles("embedding")
      .then(setEmbProfiles)
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const set = <K extends keyof KbFormState>(k: K, v: KbFormState[K]) =>
    setS((p) => ({ ...p, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (llmProfileId === null) return;
    setBusy(true);
    setError(null);
    try {
      const settingsObj = buildSettings(s); // throws on bad advancedOverride
      const settings_yaml = JSON.stringify(settingsObj);
      if (isEdit && kb) {
        await updateKb(kb.id, {
          name,
          method: s.method,
          settings_yaml,
          llm_profile_id: llmProfileId,
          embedding_profile_id: embeddingProfileId,
        });
        onSaved?.();
      } else {
        const created = await createKb({
          name,
          method: s.method,
          settings_yaml,
          llm_profile_id: llmProfileId,
          embedding_profile_id: embeddingProfileId,
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

      {/* Provider 配置 */}
      <details open>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          Provider 配置
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="LLM 配置" hint="在 Provider 配置页新建">
            <select
              className="select"
              value={llmProfileId ?? ""}
              onChange={(e) =>
                setLlmProfileId(e.target.value ? Number(e.target.value) : null)
              }
            >
              <option value="">请选择 LLM profile…</option>
              {llmProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Embedding 配置" hint="local/basic/drift 需要；可留空">
            <select
              className="select"
              value={embeddingProfileId ?? ""}
              onChange={(e) =>
                setEmbeddingProfileId(e.target.value ? Number(e.target.value) : null)
              }
            >
              <option value="">无</option>
              {embProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </select>
          </Field>
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
          <p className="self-end text-[12px] text-muted">
            structured_output 由所选 LLM profile 决定（在 Provider 配置页设置）。
          </p>
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
                placeholder='{"chunking":{"size":1200}}'
              />
            </Field>
          </div>
        )}
      </div>

      {error && (
        <p className="text-[13px] text-danger">{isEdit ? "保存失败" : "创建失败"}：{error}</p>
      )}
      <Button
        type="submit"
        variant="primary"
        disabled={busy || llmProfileId === null}
        className="w-full"
      >
        <IconPlus width={16} height={16} />
        {busy
          ? isEdit
            ? "保存中…"
            : "创建中…"
          : isEdit
            ? "保存修改"
            : "创建知识库"}
      </Button>
    </form>
  );
}
