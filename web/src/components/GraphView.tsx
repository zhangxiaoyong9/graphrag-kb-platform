import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getGraph } from "../api/client";
import type { GraphData } from "../api/types";
import { Button, EmptyState } from "./ui";
import { IconSearch, IconGraph } from "./icons";

// Hash a community string to a stable hue (0-359) for coloring nodes.
function communityHue(community: string): number {
  let h = 0;
  for (let i = 0; i < community.length; i++) h = (h * 31 + community.charCodeAt(i)) % 360;
  return h;
}

interface GraphViewProps {
  kbId: number;
  limit?: number;
}

/** Interactive force-directed graph (react-force-graph-2d) with a search box. */
export function GraphView({ kbId, limit = 100 }: GraphViewProps) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fgRef = useRef<{ zoomToFit?: (ms?: number, pad?: number) => void } | undefined>(undefined);

  const fetchGraph = (params: { q?: string; hop?: number }) => {
    setLoading(true);
    getGraph(kbId, { limit, ...params })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String((e as Error).message ?? e)))
      .finally(() => setLoading(false));
  };

  // Initial fetch (no search filter).
  useEffect(() => {
    fetchGraph({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbId, limit]);

  // Map edges -> links with source/target referencing node ids.
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    return {
      nodes: data.nodes.map((n) => ({
        id: n.id,
        title: n.title,
        type: n.type,
        degree: n.degree,
        community: n.community,
        color: `hsl(${communityHue(n.community ?? "")}, 70%, 55%)`,
      })),
      links: data.edges.map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
        description: e.description,
      })),
    };
  }, [data]);

  const nodeCount = data?.nodes.length ?? 0;
  const edgeCount = data?.edges.length ?? 0;
  const capped = nodeCount >= limit;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <IconSearch
            width={16}
            height={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
          />
          <input
            aria-label="搜索实体"
            className="input pl-9"
            placeholder="搜索实体…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") fetchGraph({ q, hop: 2 });
            }}
          />
        </div>
        <Button variant="primary" onClick={() => fetchGraph({ q, hop: 2 })}>
          <IconSearch width={16} height={16} /> 搜索
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span className="nums">共 {nodeCount} 个节点</span>
        <span className="nums">{edgeCount} 条关系</span>
        {capped && <span className="text-warning">已达上限，可缩小搜索范围或提高 limit</span>}
      </div>

      {error && (
        <div className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          图谱加载失败：{error}
        </div>
      )}

      <div className="relative overflow-hidden rounded-xl border border-line bg-surface" style={{ height: 480 }}>
        {nodeCount === 0 && !loading && !error && (
          <div className="absolute inset-0 z-10 flex items-center justify-center p-6">
            <EmptyState
              icon={<IconGraph />}
              title="暂无图谱数据"
              hint="先触发一次索引任务，抽取实体与关系后即可在此可视化。"
            />
          </div>
        )}
        {loading && (
          <div className="absolute right-3 top-3 z-10 rounded-full bg-surface/90 px-3 py-1 text-xs text-muted shadow-soft">
            加载中…
          </div>
        )}
        <ForceGraph2D
          ref={fgRef as never}
          graphData={graphData as never}
          nodeLabel="title"
          nodeColor="color"
          linkWidth={(l: { weight?: number }) => l.weight ?? 1}
          onEngineStop={() => fgRef.current?.zoomToFit?.(400, 20)}
          cooldownTicks={100}
        />
      </div>
    </div>
  );
}
