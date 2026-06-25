import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { loadAllJobs, loadAllCost, loadAllDocuments } from "../lib/aggregate";
import type { AllDocuments } from "../lib/aggregate";
import { getGraph } from "../api/client";
import type { GraphNode } from "../api/types";
import { humanBytes, moneyCompact } from "../lib/format";
import { Card, CardHeader, Stat, EmptyState, Spinner } from "../components/ui";
import { IconChart, IconDatabase, IconDoc, IconTask, IconCost, IconGraph, IconClock } from "../components/icons";

const STATUS_ORDER = ["succeeded", "partially_failed", "failed", "running", "pending", "cancelled"] as const;
const STATUS_LABEL: Record<string, string> = {
  succeeded: "成功",
  partially_failed: "部分失败",
  failed: "失败",
  running: "运行中",
  pending: "待处理",
  cancelled: "已取消",
};

/** Analytics: real aggregates from existing endpoints + honest empty states. */
export default function AnalyticsPage() {
  const jobs = useAsync(() => loadAllJobs(), []);
  const cost = useAsync(() => loadAllCost().catch(() => null), []);
  const docs = useAsync(() => loadAllDocuments().catch(() => null), []);

  const allJobs = jobs.data ?? [];
  const terminal = allJobs.filter((j) => j.status !== "running" && j.status !== "pending");
  const succeeded = allJobs.filter((j) => j.status === "succeeded").length;
  const successRate = terminal.length > 0 ? Math.round((succeeded / terminal.length) * 100) : null;

  // status distribution (real)
  const dist = STATUS_ORDER.map((s) => ({ s, n: allJobs.filter((j) => j.status === s).length })).filter(
    (x) => x.n > 0,
  );
  const distMax = Math.max(1, ...dist.map((x) => x.n));

  return (
    <div className="space-y-5">
      {/* Real top-line stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="知识库" value={docs.data?.kbs.length ?? "—"} icon={<IconDatabase width={18} height={18} />} />
        <Stat label="文档" value={docs.data?.totalDocs ?? "—"} sub={docs.data ? `${humanBytes(docs.data.totalBytes)}` : undefined} icon={<IconDoc width={18} height={18} />} />
        <Stat label="任务" value={allJobs.length} icon={<IconTask width={18} height={18} />} />
        <Stat
          label="成功率"
          value={successRate == null ? "—" : `${successRate}%`}
          sub={successRate == null ? "无已完成任务" : `${succeeded}/${terminal.length} 已完成`}
          icon={<IconChart width={18} height={18} />}
          accent
        />
        <Stat label="累计成本" value={moneyCompact(cost.data?.totalUsd ?? null)} icon={<IconCost width={18} height={18} />} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Real status distribution */}
        <Card>
          <CardHeader title="任务状态分布" subtitle="跨所有知识库（真实数据）" icon={<IconTask width={18} height={18} />} />
          <div className="mt-4">
            {jobs.loading ? (
              <p className="flex items-center gap-2 text-[13px] text-muted"><Spinner /> 加载…</p>
            ) : dist.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
                还没有任务
              </p>
            ) : (
              <ul className="space-y-2.5">
                {dist.map((x) => (
                  <li key={x.s} className="flex items-center gap-3">
                    <span className="w-20 shrink-0 text-[13px] text-body">{STATUS_LABEL[x.s]}</span>
                    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className="h-full rounded-full bg-brand"
                        style={{ width: `${(x.n / distMax) * 100}%` }}
                      />
                    </div>
                    <span className="nums w-8 shrink-0 text-right text-[13px] text-ink">{x.n}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>

        {/* Honest empty: no timestamps */}
        <Card>
          <CardHeader title="任务趋势" icon={<IconClock width={18} height={18} />} />
          <div className="mt-4">
            <EmptyState
              icon={<IconClock />}
              title="暂无法绘制趋势"
              hint="任务记录不含时间戳（created_at），当前接口无法按时间聚合。该指标将在后续版本补齐。"
            />
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Honest empty: no query log */}
        <Card>
          <CardHeader title="热门查询" icon={<IconChart width={18} height={18} />} />
          <div className="mt-4">
            <EmptyState
              icon={<IconChart />}
              title="暂无查询历史"
              hint="当前版本未记录查询日志，无法统计热门查询。该指标将在后续版本补齐。"
            />
          </div>
        </Card>

        {/* Real hot entities (largest KB by docs) */}
        <HotEntities docs={docs.data} />
      </div>

      {/* Per-KB breakdown (real) */}
      <Card>
        <CardHeader title="知识库明细" subtitle="每个知识库的文档 / 任务 / 成本（真实）" icon={<IconDatabase width={18} height={18} />} />
        <div className="mt-4 overflow-hidden rounded-xl border border-line">
          <table className="w-full text-[13px]">
            <thead className="bg-surface-2 text-left text-[12px] text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">知识库</th>
                <th className="px-4 py-2 text-right font-medium">文档</th>
                <th className="px-4 py-2 text-right font-medium">任务</th>
                <th className="px-4 py-2 text-right font-medium">累计成本</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {(docs.data?.kbs ?? []).map((k) => {
                const jobN = allJobs.filter((j) => j.kbId === k.id).length;
                const usd = cost.data?.kbs.find((c) => c.id === k.id)?.totalUsd ?? null;
                return (
                  <tr key={k.id} className="hover:bg-surface-2/50">
                    <td className="px-4 py-2.5">
                      <Link to={`/kbs/${k.id}`} className="font-medium text-ink hover:text-brand">{k.name}</Link>
                      <span className="ml-1.5 text-[11px] text-muted nums">{k.method}</span>
                    </td>
                    <td className="nums px-4 py-2.5 text-right text-ink">{k.docs.length}</td>
                    <td className="nums px-4 py-2.5 text-right text-ink">{jobN}</td>
                    <td className="nums px-4 py-2.5 text-right text-ink">{moneyCompact(usd)}</td>
                  </tr>
                );
              })}
              {(docs.data?.kbs.length ?? 0) === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-muted">
                    {docs.loading ? "加载中…" : "还没有知识库"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

/** Top entities for the KB with the most documents — real data, single request. */
function HotEntities({ docs }: { docs: AllDocuments | null }) {
  const [nodes, setNodes] = useState<GraphNode[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [kbName, setKbName] = useState<string | null>(null);

  const target = (docs?.kbs ?? [])
    .slice()
    .sort((a, b) => b.docs.length - a.docs.length)[0];

  useEffect(() => {
    let alive = true;
    if (!target) {
      setNodes(null);
      return;
    }
    setLoading(true);
    setKbName(target.name);
    getGraph(target.id, { limit: 12 })
      .then((g) => {
        if (alive) setNodes([...g.nodes].sort((a, b) => b.degree - a.degree).slice(0, 8));
      })
      .catch(() => {
        if (alive) setNodes(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [target?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Card>
      <CardHeader
        title="热门实体"
        subtitle={kbName ? `来源：${kbName}（文档最多）` : "按 degree 排序"}
        icon={<IconGraph width={18} height={18} />}
      />
      <div className="mt-4">
        {loading ? (
          <p className="flex items-center gap-2 text-[13px] text-muted"><Spinner /> 加载…</p>
        ) : !nodes || nodes.length === 0 ? (
          <EmptyState icon={<IconGraph />} title="暂无图谱数据" hint="完成索引任务后，高关联实体会显示在这里。" />
        ) : (
          <div className="flex flex-wrap gap-2">
            {nodes.map((n) => (
              <span
                key={n.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[12px]"
              >
                <span className="font-medium text-ink">{n.title}</span>
                <span className="nums text-muted">·{n.degree}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
