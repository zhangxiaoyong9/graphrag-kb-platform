import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getGraph } from "../api/client";
import type { GraphEdge, GraphNode } from "../api/types";
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from "../components/ui";
import { IconArrowLeft, IconGraph } from "../components/icons";
import { useAsync } from "../hooks/useAsync";
import { useKb } from "./kb-context";

export default function EntityRelationPage() {
  const { kbId } = useKb();
  const { docId } = useParams();
  const graph = useAsync(() => getGraph(kbId, { limit: 200 }), [kbId]);
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);

  const nodes = graph.data?.nodes ?? [];
  const edges = graph.data?.edges ?? [];
  const relatedEdges = useMemo(() => {
    if (!selectedEntity) return edges;
    return edges.filter((edge) => edge.source === selectedEntity || edge.target === selectedEntity);
  }, [edges, selectedEntity]);

  if (graph.loading) {
    return (
      <Card>
        <CardHeader title="实体 / 关系" subtitle="加载抽取结果…" icon={<IconGraph width={18} height={18} />} />
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      </Card>
    );
  }

  if (graph.error) {
    return (
      <EmptyState
        icon={<IconGraph />}
        title="实体关系加载失败"
        hint={graph.error}
        action={<BackToDocument docId={docId} />}
      />
    );
  }

  if (nodes.length === 0 && edges.length === 0) {
    return (
      <EmptyState
        icon={<IconGraph />}
        title="暂无实体或关系"
        hint="索引可能仍在运行，或当前知识库没有抽取到可浏览的结构化结果。"
        action={<BackToDocument docId={docId} />}
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BackToDocument docId={docId} />
        {selectedEntity && (
          <Button size="sm" variant="ghost" onClick={() => setSelectedEntity(null)}>
            清除选择
          </Button>
        )}
      </div>

      <Card>
        <CardHeader
          title="实体 / 关系"
          subtitle="独立浏览抽取结果；选择实体后查看相关关系。"
          icon={<IconGraph width={18} height={18} />}
          actions={selectedEntity ? <Badge tone="info">已选择实体：{selectedEntity}</Badge> : <Badge>{nodes.length} 实体</Badge>}
        />

        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <EntityList nodes={nodes} selectedEntity={selectedEntity} onSelect={setSelectedEntity} />
          <RelationshipList edges={relatedEdges} onSelectEntity={setSelectedEntity} />
        </div>
      </Card>
    </div>
  );
}

function BackToDocument({ docId }: { docId: string | undefined }) {
  const { kbId } = useKb();
  return (
    <Link to={`/kbs/${kbId}/documents/${docId ?? ""}`} className="inline-flex items-center gap-1 text-[13px] font-medium text-brand hover:underline">
      <IconArrowLeft width={14} height={14} /> 返回文档
    </Link>
  );
}

function EntityList({
  nodes,
  selectedEntity,
  onSelect,
}: {
  nodes: GraphNode[];
  selectedEntity: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section>
      <h4 className="mb-3 text-sm font-semibold text-ink">实体</h4>
      <ul className="space-y-2">
        {nodes.map((node) => (
          <li key={node.id}>
            <button
              className={
                "w-full rounded-xl border px-3 py-3 text-left transition-colors " +
                (selectedEntity === node.id
                  ? "border-brand bg-brand-grad-soft"
                  : "border-line bg-surface hover:bg-surface-2")
              }
              onClick={() => onSelect(node.id)}
              aria-label={`查看实体 ${node.title} 的关系`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">{node.title}</p>
                  <p className="mt-1 text-xs text-muted">{node.type || "未分类"}</p>
                </div>
                <Badge tone="neutral">度 {node.degree}</Badge>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RelationshipList({ edges, onSelectEntity }: { edges: GraphEdge[]; onSelectEntity: (id: string) => void }) {
  return (
    <section>
      <h4 className="mb-3 text-sm font-semibold text-ink">关系</h4>
      {edges.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
          当前实体没有可显示关系。
        </p>
      ) : (
        <ul className="space-y-2">
          {edges.map((edge) => (
            <li key={`${edge.source}-${edge.target}-${edge.description}`}>
              <button
                className="w-full rounded-xl border border-line bg-surface px-3 py-3 text-left hover:bg-surface-2"
                onClick={() => onSelectEntity(String(edge.source))}
                aria-label={`查看关系 ${edge.source} 到 ${edge.target}`}
              >
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
                  <span>{String(edge.source)}</span>
                  <span className="text-muted">→</span>
                  <span>{String(edge.target)}</span>
                </div>
                <p className="mt-1 text-[13px] leading-5 text-muted">{edge.description || "无关系描述"}</p>
                <p className="mt-2 text-xs text-muted">权重 {edge.weight}</p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
