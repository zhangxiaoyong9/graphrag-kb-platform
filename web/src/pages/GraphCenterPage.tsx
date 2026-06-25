import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { listKbs, getGraph } from "../api/client";
import type { GraphNode } from "../api/types";
import { Card, CardHeader, EmptyState, Spinner } from "../components/ui";
import { GraphView } from "../components/GraphView";
import { IconGraph, IconDatabase, IconChevronRight } from "../components/icons";

/** Cross-KB graph entry: pick a KB → reuse GraphView + real Top-N entity overview. */
export default function GraphCenterPage() {
  const kbs = useAsync(() => listKbs(), []);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const list = kbs.data ?? [];

  // Default-select the first KB once loaded.
  useEffect(() => {
    if (selectedId == null && list.length > 0) setSelectedId(list[0].id);
  }, [list, selectedId]);

  if (kbs.loading && list.length === 0) {
    return (
      <div className="card card-pad flex items-center gap-2 text-sm text-muted">
        <Spinner /> 加载知识库…
      </div>
    );
  }

  if (list.length === 0) {
    return (
      <EmptyState
        icon={<IconGraph />}
        title="还没有知识库"
        hint="先创建知识库并触发一次索引，抽取实体与关系后即可在此可视化。"
        action={<Link to="/kbs" className="btn btn-primary btn-sm">前往知识库管理</Link>}
      />
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="图谱管理"
          subtitle="选择知识库查看实体-关系图谱；颜色按社区聚类"
          icon={<IconGraph width={18} height={18} />}
        />
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-[13px] text-muted">
            <IconDatabase width={15} height={15} /> 知识库
          </span>
          <div className="flex flex-wrap gap-1.5">
            {list.map((k) => (
              <button
                key={k.id}
                onClick={() => setSelectedId(k.id)}
                className={
                  "rounded-full border px-3 py-1 text-[13px] transition-colors " +
                  (selectedId === k.id
                    ? "border-brand bg-brand text-white"
                    : "border-line-strong bg-surface text-body hover:bg-surface-2")
                }
              >
                {k.name}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {selectedId != null && (
        <div className="grid gap-5 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader
              title="图谱可视化"
              icon={<IconGraph width={18} height={18} />}
              actions={
                <Link
                  to={`/kbs/${selectedId}/graph`}
                  className="inline-flex items-center gap-1 text-[13px] font-medium text-brand hover:underline"
                >
                  打开 KB 图谱 <IconChevronRight width={14} height={14} />
                </Link>
              }
            />
            <div className="mt-4">
              <GraphView kbId={selectedId} limit={120} />
            </div>
          </Card>

          <TopEntities kbId={selectedId} />
        </div>
      )}
    </div>
  );
}

function TopEntities({ kbId }: { kbId: number }) {
  const [nodes, setNodes] = useState<GraphNode[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getGraph(kbId, { limit: 12 })
      .then((g) => {
        if (alive) setNodes([...g.nodes].sort((a, b) => b.degree - a.degree).slice(0, 10));
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
  }, [kbId]);

  return (
    <Card>
      <CardHeader title="高关联实体" subtitle="按 degree 排序 Top-10（真实数据）" icon={<IconGraph width={18} height={18} />} />
      <div className="mt-4">
        {loading ? (
          <p className="flex items-center gap-2 text-[13px] text-muted">
            <Spinner /> 加载实体…
          </p>
        ) : !nodes || nodes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
            暂无图谱数据，先触发一次索引任务。
          </p>
        ) : (
          <ul className="space-y-1.5">
            {nodes.map((n, i) => (
              <li key={n.id} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-surface-2">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand-grad-soft text-[11px] font-semibold text-brand nums">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{n.title}</p>
                  <p className="text-[11px] text-muted">{n.type}</p>
                </div>
                <span className="nums shrink-0 text-[12px] text-muted">度 {n.degree}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
