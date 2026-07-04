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
          queryDefaults: { ...DEFAULTS.queryDefaults },
        },
  );
  const [name, setName] = useState(kb?.name ?? "");
  const [dataRoot, setDataRoot] = useState("");
  const [llmProfileId, setLlmProfileId] = useState<number | null>(
    kb?.llm_profile?.id ?? null,
  );
  const [embeddingProfileId, setEmbeddingProfileId] = useState<number | null>(
    kb?.embedding_profile?.id ?? null,
  );
  const [llmFallbackIds, setLlmFallbackIds] = useState<number[]>(
    kb?.llm_fallback_profile_ids ?? [],
  );
  const [fallbackAddId, setFallbackAddId] = useState<string>("");
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
          llm_fallback_profile_ids: llmFallbackIds,
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
          llm_fallback_profile_ids: llmFallbackIds,
          ...(dataRoot.trim() ? { data_root: dataRoot.trim() } : {}),
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
      {!isEdit && (
        <Field label="数据目录（可选）" hint="留空 = 自动按 KB 隔离">
          <input
            className="input"
            placeholder="留空 = 自动按 KB 隔离"
            value={dataRoot}
            onChange={(e) => setDataRoot(e.target.value)}
            aria-label="data_root"
          />
        </Field>
      )}
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

        {/* 故障转移 LLM Profile（按顺序）—— ordered multi-select.
            Primary (llmProfileId) is excluded from the add-options; the order
            of selection is preserved and submitted as llm_fallback_profile_ids. */}
        <Field
          label="故障转移 LLM Profile（按顺序）"
          hint="主 Profile 失败（报错/限流）时，按此顺序自动切换到下一个。可不选。"
        >
          <div className="space-y-2" data-testid="llm-fallback-list">
            {llmFallbackIds.length === 0 && (
              <p className="text-[12px] text-muted">未配置故障转移</p>
            )}
            {llmFallbackIds.map((pid, idx) => {
              const p = llmProfiles.find((x) => x.id === pid);
              return (
                <div
                  key={pid}
                  className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px]"
                >
                  <span className="font-mono text-muted">{idx + 1}.</span>
                  <span className="flex-1 truncate">
                    {p ? `${p.name} · ${p.model}` : `#${pid}（未加载）`}
                  </span>
                  <button
                    type="button"
                    aria-label={`上移 ${p?.name ?? pid}`}
                    disabled={idx === 0}
                    className="rounded px-1.5 py-0.5 text-muted hover:bg-surface-2 disabled:opacity-30"
                    onClick={() =>
                      setLlmFallbackIds((arr) => {
                        if (idx === 0) return arr;
                        const next = [...arr];
                        [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                        return next;
                      })
                    }
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`下移 ${p?.name ?? pid}`}
                    disabled={idx === llmFallbackIds.length - 1}
                    className="rounded px-1.5 py-0.5 text-muted hover:bg-surface-2 disabled:opacity-30"
                    onClick={() =>
                      setLlmFallbackIds((arr) => {
                        if (idx === arr.length - 1) return arr;
                        const next = [...arr];
                        [next[idx + 1], next[idx]] = [next[idx], next[idx + 1]];
                        return next;
                      })
                    }
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label={`移除 ${p?.name ?? pid}`}
                    className="rounded px-1.5 py-0.5 text-danger hover:bg-surface-2"
                    onClick={() =>
                      setLlmFallbackIds((arr) => arr.filter((x) => x !== pid))
                    }
                  >
                    ✕
                  </button>
                </div>
              );
            })}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <select
              className="select flex-1"
              aria-label="添加故障转移 LLM Profile"
              value={fallbackAddId}
              onChange={(e) => setFallbackAddId(e.target.value)}
            >
              <option value="">＋ 添加 LLM Profile…</option>
              {llmProfiles
                .filter(
                  (p) =>
                    p.id !== llmProfileId && !llmFallbackIds.includes(p.id),
                )
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.model}
                  </option>
                ))}
            </select>
            <button
              type="button"
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] text-body hover:bg-surface-2 disabled:opacity-40"
              disabled={fallbackAddId === ""}
              onClick={() => {
                const id = Number(fallbackAddId);
                if (!id) return;
                setLlmFallbackIds((arr) =>
                  arr.includes(id) ? arr : [...arr, id],
                );
                setFallbackAddId("");
              }}
            >
              添加
            </button>
          </div>
        </Field>
      </details>

      {/* 分块 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          分块 Chunking
        </summary>
        <div className="mt-3">
          <Field
            label="切片方式"
            hint={
              s.chunking.strategy !== "tokens" ? "结构切分不使用此项" : undefined
            }
          >
            <select
              className="select"
              value={s.chunking.strategy}
              onChange={(e) =>
                set("chunking", { ...s.chunking, strategy: e.target.value })
              }
            >
              <option value="markdown">按结构（段落 / 表格不断开）</option>
              <option value="tokens">按 token 数</option>
            </select>
          </Field>
        </div>
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
              disabled={s.chunking.strategy !== "tokens"}
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

      {/* 检索默认值 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          检索默认值 Query Defaults（留空=默认；查询时可按次覆盖）
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="community_level" hint="0–4；越大越细">
            <input className="input" type="number" min={0} max={4}
              value={s.queryDefaults.communityLevel}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, communityLevel: e.target.value })}
              placeholder="留空=2" />
          </Field>
          <Field label="response_type" hint="多段/单段/要点">
            <select className="select"
              value={s.queryDefaults.responseType}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, responseType: e.target.value })}>
              <option value="">留空=默认</option>
              <option value="multiple paragraphs">多段</option>
              <option value="single paragraph">单段</option>
              <option value="bullet points">要点</option>
            </select>
          </Field>
          <Field label="top_k" hint="local/basic 结果数">
            <input className="input" type="number" min={1}
              value={s.queryDefaults.topK}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, topK: e.target.value })}
              placeholder="留空=默认" />
          </Field>
          <Field label="temperature" hint="0–1">
            <input className="input" type="number" step="0.05" min={0} max={1}
              value={s.queryDefaults.temperature}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, temperature: e.target.value })}
              placeholder="留空=默认" />
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
