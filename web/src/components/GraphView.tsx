import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getGraph } from "../api/client";
import type { GraphData } from "../api/types";

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

export function GraphView({ kbId, limit = 100 }: GraphViewProps) {
  const [data, setData] = useState<GraphData | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fgRef = useRef<{ zoomToFit?: (ms?: number, pad?: number) => void } | undefined>(undefined);

  const fetchGraph = (params: { q?: string; hop?: number }) => {
    getGraph(kbId, { limit, ...params })
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(String(e.message ?? e)));
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
        color: `hsl(${communityHue(n.community)}, 70%, 55%)`,
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
  const capped = nodeCount >= limit;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          aria-label="graph search"
          className="border p-1 rounded flex-1"
          placeholder="search entities…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") fetchGraph({ q, hop: 2 });
          }}
        />
        <button
          className="bg-blue-600 text-white px-3 py-1 rounded"
          onClick={() => fetchGraph({ q, hop: 2 })}
        >
          Search
        </button>
      </div>
      <div className="text-xs text-gray-500">
        showing {nodeCount} nodes{capped ? " (capped — refine search or raise limit)" : ""}
      </div>
      {error && <div className="text-red-600 text-sm">graph error: {error}</div>}
      <div className="border rounded bg-white" style={{ height: 460 }}>
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
