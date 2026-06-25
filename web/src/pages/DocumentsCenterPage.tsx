import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { loadAllDocuments } from "../lib/aggregate";
import { humanBytes } from "../lib/format";
import { Card, CardHeader, Stat, EmptyState, Badge, Spinner } from "../components/ui";
import { IconDoc, IconDatabase, IconChevronRight } from "../components/icons";

/** Cross-KB document center. Aggregates per-KB documents via existing endpoints only. */
export default function DocumentsCenterPage() {
  const data = useAsync(() => loadAllDocuments(), []);
  const d = data.data;

  if (data.loading && !d) {
    return (
      <div className="card card-pad flex items-center gap-2 text-sm text-muted">
        <Spinner /> 加载文档…
      </div>
    );
  }

  if (d && d.kbs.length === 0) {
    return (
      <EmptyState
        icon={<IconDoc />}
        title="还没有知识库"
        hint="先在「知识库管理」创建一个知识库，再回到这里管理文档。"
        action={
          <Link to="/kbs" className="btn btn-primary btn-sm">
            前往知识库管理
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="知识库" value={d?.kbs.length ?? "—"} icon={<IconDatabase width={18} height={18} />} />
        <Stat label="文档总数" value={d?.totalDocs ?? "—"} icon={<IconDoc width={18} height={18} />} />
        <Stat label="分块总数" value={d?.totalChunks ?? "—"} icon={<IconDoc width={18} height={18} />} />
        <Stat label="总大小" value={d ? humanBytes(d.totalBytes) : "—"} icon={<IconDoc width={18} height={18} />} />
      </div>

      {d?.kbs.map((k) => (
        <Card key={k.id}>
          <CardHeader
            title={
              <span className="flex items-center gap-2">
                {k.name}
                <Badge tone="brand">{k.method}</Badge>
              </span>
            }
            subtitle={`${k.docs.length} 个文档 · ${humanBytes(k.docs.reduce((s, x) => s + x.bytes, 0))}`}
            icon={<IconDatabase width={18} height={18} />}
            actions={
              <Link to={`/kbs/${k.id}/documents`} className="text-[13px] font-medium text-brand hover:underline">
                进入文档管理
              </Link>
            }
          />
          <div className="mt-4">
            {k.docs.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line-strong px-3 py-5 text-center text-[13px] text-muted">
                该知识库暂无文档
              </p>
            ) : (
              <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
                {k.docs.slice(0, 8).map((doc) => (
                  <li key={doc.id} className="flex items-center gap-3 px-4 py-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-brand">
                      <IconDoc width={15} height={15} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-ink">{doc.title}</p>
                      <p className="nums text-[11px] text-muted">
                        {humanBytes(doc.bytes)} · {doc.chunk_count} 个分块
                      </p>
                    </div>
                    {doc.status && <Badge tone="neutral">{doc.status}</Badge>}
                  </li>
                ))}
                {k.docs.length > 8 && (
                  <li className="bg-surface-2/40 px-4 py-2 text-center text-[12px] text-muted">
                    <Link to={`/kbs/${k.id}/documents`} className="inline-flex items-center gap-1 text-brand hover:underline">
                      查看全部 {k.docs.length} 个文档 <IconChevronRight width={13} height={13} />
                    </Link>
                  </li>
                )}
              </ul>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}
