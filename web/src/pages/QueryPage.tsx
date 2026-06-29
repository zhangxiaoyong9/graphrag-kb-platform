import { useState } from "react";
import { useKb } from "./kb-context";
import { query as apiQuery } from "../api/client";
import type { QueryResult } from "../api/types";
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

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await apiQuery(kbId, method, q);
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
