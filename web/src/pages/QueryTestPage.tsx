import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { listKbs, query as apiQuery } from "../api/client";
import type { QueryResult } from "../api/types";
import { QUERY_METHODS } from "../lib/query-methods";
import { cn } from "../lib/cn";
import { Card, CardHeader, Button, Spinner, Badge, EmptyState } from "../components/ui";
import { IconSearch, IconSparkle, IconWarn, IconClock, IconDatabase } from "../components/icons";

interface Result {
  data: QueryResult;
  elapsedMs: number;
  method: string;
}

/** Top-level retrieval test: pick a KB + method, ask, see answer + real round-trip time. */
export default function QueryTestPage() {
  const kbs = useAsync(() => listKbs(), []);
  const list = kbs.data ?? [];

  const [kbId, setKbId] = useState<number | null>(null);
  const [method, setMethod] = useState("local");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (kbId == null && list.length > 0) setKbId(list[0].id);
  }, [list, kbId]);

  const ask = async () => {
    if (kbId == null || !q.trim()) return;
    setBusy(true);
    setError(null);
    const t0 = performance.now();
    try {
      const r = await apiQuery(kbId, method, q);
      const elapsedMs = performance.now() - t0;
      setResult({ data: r, elapsedMs, method });
      if (r.error) setError(r.error);
    } catch (e) {
      setError((e as Error).message ?? String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  if (list.length === 0 && !kbs.loading) {
    return (
      <EmptyState
        icon={<IconSearch />}
        title="还没有可检索的知识库"
        hint="先创建知识库并完成一次索引，再回来进行检索测试。"
        action={<Link to="/kbs" className="btn btn-primary btn-sm">前往知识库管理</Link>}
      />
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="检索测试"
          subtitle="选择知识库与检索方式，查看答案与耗时"
          icon={<IconSearch width={18} height={18} />}
        />
        <div className="mt-5 space-y-4">
          {/* KB picker */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-[13px] text-muted">
              <IconDatabase width={15} height={15} /> 知识库
            </span>
            <div className="flex flex-wrap gap-1.5">
              {list.map((k) => (
                <button
                  key={k.id}
                  onClick={() => setKbId(k.id)}
                  className={
                    "rounded-full border px-3 py-1 text-[13px] transition-colors " +
                    (kbId === k.id
                      ? "border-brand bg-brand text-white"
                      : "border-line-strong bg-surface text-body hover:bg-surface-2")
                  }
                >
                  {k.name}
                </button>
              ))}
              {kbs.loading && <span className="text-[12px] text-muted">加载中…</span>}
            </div>
          </div>

          {/* Methods */}
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

          {/* Input */}
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
              <Button variant="primary" disabled={busy || !q.trim() || kbId == null} onClick={ask}>
                {busy ? <Spinner /> : <IconSparkle width={16} height={16} />}
                {busy ? "查询中…" : "提问"}
              </Button>
            </div>
          </div>
        </div>
      </Card>

      {(result || error) && (
        <Card>
          <CardHeader
            title="回答"
            icon={<IconSparkle width={18} height={18} />}
            actions={
              result && (
                <span className="flex items-center gap-2">
                  <Badge tone="brand">{result.method}</Badge>
                  <span className="flex items-center gap-1 text-[12px] text-muted nums">
                    <IconClock width={13} height={13} /> {result.elapsedMs.toFixed(0)} ms
                  </span>
                </span>
              )
            }
          />
          <div className="mt-4 space-y-3">
            {error ? (
              <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
                <IconWarn width={16} height={16} className="mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            ) : (
              <div className="whitespace-pre-wrap rounded-xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink">
                {result?.data.answer}
              </div>
            )}
            {/* Honest: the query API returns a synthesized answer with no structured citations. */}
            <div className="rounded-lg border border-dashed border-line-strong px-3 py-2 text-[12px] text-muted">
              <span className="font-medium text-body">引用片段：</span>
              当前查询接口仅返回综合答案，未附带结构化引用 / 来源片段，故此处不展示。
              <Link to={`/kbs/${kbId ?? ""}/graph`} className="ml-1 text-brand hover:underline">
                前往图谱查看相关实体 →
              </Link>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
