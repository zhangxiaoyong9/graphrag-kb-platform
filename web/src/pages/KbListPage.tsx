import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { listKbs } from "../api/client";
import { Card, CardHeader, EmptyState, Badge } from "../components/ui";
import KbForm from "../components/KbForm";
import { IconDatabase, IconChevronRight, IconPlus } from "../components/icons";

/** KB management: grid of knowledge bases + create panel. */
export default function KbListPage() {
  const kbs = useAsync(() => listKbs(), []);
  const list = kbs.data ?? [];

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {list.length === 0 ? (
            <EmptyState
              icon={<IconDatabase />}
              title="还没有知识库"
              hint="在右侧创建你的第一个知识库，开始从文档构建图谱。"
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {list.map((k) => (
                <Link
                  key={k.id}
                  to={`/kbs/${k.id}`}
                  className="card group flex items-center gap-3 p-4 transition-all hover:-translate-y-0.5 hover:shadow-pop"
                >
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-grad-soft text-brand">
                    <IconDatabase width={20} height={20} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[15px] font-semibold text-ink">{k.name}</span>
                      <Badge tone="brand">{k.method}</Badge>
                    </div>
                    <p className="mt-0.5 text-[12px] text-muted nums">ID · {k.id}</p>
                  </div>
                  <IconChevronRight
                    width={18}
                    height={18}
                    className="text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-brand"
                  />
                </Link>
              ))}
            </div>
          )}
        </div>

        <Card className="lg:col-span-1">
          <CardHeader title="创建知识库" subtitle="配置模型与索引方法" icon={<IconPlus width={18} height={18} />} />
          <div className="mt-4">
            <KbForm onCreated={kbs.reload} />
          </div>
        </Card>
      </div>
    </div>
  );
}
