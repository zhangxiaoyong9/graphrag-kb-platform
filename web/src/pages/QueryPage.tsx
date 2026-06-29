import { useEffect, useMemo, useState } from "react";
import { useKb } from "./kb-context";
import { query as apiQuery, listQueryPresets, createQueryPreset } from "../api/client";
import type { QueryResult, QueryParams, QueryPreset } from "../api/types";
import { QUERY_METHODS } from "../lib/query-methods";
import { parseSse } from "../lib/sse";
import { cn } from "../lib/cn";
import { Card, CardHeader, Button, Spinner } from "../components/ui";
import { QueryResultView } from "../components/QueryResultView";
import { IconSparkle } from "../components/icons";

/** Query tab: pick a retrieval method, ask, read the answer. */
export default function QueryPage() {
  const { kbId } = useKb();
  const [method, setMethod] = useState("local");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Tuning / preset state (A3).
  const [showTune, setShowTune] = useState(false);
  const [presets, setPresets] = useState<QueryPreset[]>([]);
  const [cl, setCl] = useState("");
  const [rt, setRt] = useState("");
  const [topK, setTopK] = useState("");
  const [temp, setTemp] = useState("");
  const [sysPrompt, setSysPrompt] = useState("");

  useEffect(() => {
    listQueryPresets().then(setPresets).catch(() => {});
  }, []);

  // Only attach params when at least one knob is set; undefined => not sent.
  const params: QueryParams | undefined = useMemo(() => {
    const p: QueryParams = {};
    if (cl.trim()) p.community_level = Number(cl);
    if (rt.trim()) p.response_type = rt;
    if (topK.trim()) p.top_k = Number(topK);
    if (temp.trim()) p.temperature = Number(temp);
    if (sysPrompt.trim()) p.system_prompt = sysPrompt;
    return Object.keys(p).length ? p : undefined;
  }, [cl, rt, topK, temp, sysPrompt]);

  const applyPreset = (p: QueryPreset | undefined) => {
    if (!p) return;
    setMethod(p.method);
    setCl(p.community_level != null ? String(p.community_level) : "");
    setRt(p.response_type ?? "");
    setTopK(p.top_k != null ? String(p.top_k) : "");
    setTemp(p.temperature != null ? String(p.temperature) : "");
    setSysPrompt(p.system_prompt ?? "");
  };

  const savePreset = async () => {
    const name = window.prompt("预设名称");
    if (!name) return;
    await createQueryPreset({
      name, description: "", method,
      community_level: cl ? Number(cl) : null,
      response_type: rt || null,
      top_k: topK ? Number(topK) : null,
      temperature: temp ? Number(temp) : null,
      system_prompt: sysPrompt || null,
    });
    setPresets(await listQueryPresets());
  };

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await apiQuery(kbId, method, q, params);
      if (!resp.ok) throw new Error(`${resp.status}`);
      let partial = "";
      let methodUsed = method;
      for await (const ev of parseSse(resp)) {
        if (ev.event === "meta") {
          methodUsed = ev.data.method ?? method;
        } else if (ev.event === "delta") {
          partial += ev.data.text ?? "";
          setResult({ answer: partial, method: methodUsed, error: null });
        } else if (ev.event === "done") {
          const data = ev.data.result as QueryResult;
          setResult(data);
          if (data.error) setError(data.error);
        } else if (ev.event === "error") {
          throw new Error(ev.data.message ?? "stream error");
        }
      }
    } catch (e) {
      setError((e as Error).message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title="检索与问答" subtitle="基于知识图谱的四种检索方式" icon={<IconSparkle width={18} height={18} />} />
        <div className="mt-5 space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {QUERY_METHODS.map((m) => (
              <button
                key={m.key}
                type="button"
                onClick={() => setMethod(m.key)}
                className={cn(
                  "rounded-xl border p-3 text-left transition-all",
                  method === m.key
                    ? "border-brand bg-brand-50/60 shadow-soft"
                    : "border-line bg-surface hover:border-line-strong hover:bg-surface-2",
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[13px] font-semibold text-ink">{m.name}</span>
                  {m.needsReports && (
                    <span className="rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] text-[#b26b00]">
                      需社区报告
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[12px] text-muted">{m.desc}</p>
              </button>
            ))}
          </div>

          <div>
            <button type="button" className="text-[13px] text-brand hover:underline"
              onClick={() => setShowTune((v) => !v)}>
              {showTune ? "隐藏调参" : "调参 / 预设"}
            </button>
            {showTune && (
              <div className="mt-3 space-y-3 rounded-xl border border-line bg-surface-2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <label className="text-[12px] text-muted">预设</label>
                  <select className="select max-w-[220px]" defaultValue="" aria-label="预设"
                    onChange={(e) => applyPreset(presets.find((p) => p.name === e.target.value))}>
                    <option value="" disabled>选择预设…</option>
                    {presets.map((p) => (
                      <option key={p.id} value={p.name}>{p.name}{p.is_builtin ? "（内置）" : ""}</option>
                    ))}
                  </select>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={savePreset}>另存为预设</button>
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <label className="text-[12px] text-muted">community_level
                    <input className="input mt-1" type="number" min={0} max={4} value={cl}
                      aria-label="community_level"
                      onChange={(e) => setCl(e.target.value)} placeholder="留空=2" />
                  </label>
                  <label className="text-[12px] text-muted">response_type
                    <select className="select mt-1" aria-label="response_type" value={rt}
                      onChange={(e) => setRt(e.target.value)}>
                      <option value="">留空=默认</option>
                      <option value="multiple paragraphs">多段</option>
                      <option value="single paragraph">单段</option>
                      <option value="bullet points">要点</option>
                    </select>
                  </label>
                  {(method === "local" || method === "basic") && (
                    <label className="text-[12px] text-muted">top_k
                      <input className="input mt-1" type="number" min={1} value={topK}
                        aria-label="top_k" onChange={(e) => setTopK(e.target.value)} placeholder="留空=默认" />
                    </label>
                  )}
                  <label className="text-[12px] text-muted">temperature
                    <input className="input mt-1" type="number" step="0.05" min={0} max={1} value={temp}
                      aria-label="temperature" onChange={(e) => setTemp(e.target.value)} placeholder="留空=默认" />
                  </label>
                </div>
                <label className="block text-[12px] text-muted">system_prompt（覆盖当前 method 主回答 prompt）
                  <textarea className="textarea mt-1 h-20 font-mono text-[12px]" value={sysPrompt}
                    aria-label="system_prompt" onChange={(e) => setSysPrompt(e.target.value)}
                    placeholder="留空=用 KB / graphrag 默认" />
                </label>
              </div>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <textarea
              className="textarea h-24"
              placeholder="输入你的问题，例如：宁德时代与特斯拉是什么关系？"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") ask();
              }}
            />
            <div className="flex items-center justify-between">
              <p className="text-[12px] text-muted">⌘/Ctrl + Enter 快速提问</p>
              <Button variant="primary" disabled={busy || !q.trim()} onClick={ask}>
                {busy ? <Spinner /> : <IconSparkle width={16} height={16} />}
                {busy ? "查询中…" : "提问"}
              </Button>
            </div>
          </div>
        </div>
      </Card>

      {(result || error) && (
        <Card>
          <CardHeader title="回答" subtitle={`方法 · ${result?.method ?? method}`} icon={<IconSparkle width={18} height={18} />} />
          <div className="mt-4 space-y-3">
            <div className="whitespace-pre-wrap rounded-xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink">
              {result?.answer}
            </div>
            <QueryResultView
              result={result ?? { answer: "", method, error: error ?? null }}
            />
          </div>
        </Card>
      )}
    </div>
  );
}
