import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { loadAllCost } from "../lib/aggregate";
import { Card, CardHeader, Stat, EmptyState } from "../components/ui";
import { money, moneyCompact } from "../lib/format";
import { IconCost, IconChevronRight } from "../components/icons";

/** Global cost statistics: total spend + per-KB breakdown. */
export default function CostPage() {
  const cost = useAsync(() => loadAllCost().catch(() => null), []);
  const data = cost.data;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <Stat label="总成本" value={moneyCompact(data?.totalUsd ?? null)} accent icon={<IconCost width={18} height={18} />} />
        <Stat label="知识库数" value={data?.kbs.length ?? "—"} icon={<IconCost width={18} height={18} />} />
        <Stat
          label="最高花费"
          value={data && data.kbs.length ? moneyCompact(data.kbs[0].totalUsd) : "—"}
          sub={data && data.kbs.length ? data.kbs[0].name : undefined}
          icon={<IconCost width={18} height={18} />}
        />
      </div>

      <Card>
        <CardHeader title="按知识库" subtitle="每个知识库的累计美元（来源：每次 LLM 调用）" icon={<IconCost width={18} height={18} />} />
        <div className="mt-4">
          {!data || data.kbs.length === 0 ? (
            <EmptyState icon={<IconCost />} title="暂无成本数据" hint="触发索引任务后，按调用采集的成本会汇总在这里。" />
          ) : (
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
              {data.kbs.map((k) => (
                <li key={k.id}>
                  <Link
                    to={`/kbs/${k.id}/cost`}
                    className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-surface-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-ink">{k.name}</p>
                      <p className="text-[11px] text-muted nums">ID · {k.id}</p>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="nums text-sm font-semibold text-ink">{money(k.totalUsd)}</span>
                      <IconChevronRight width={15} height={15} className="text-muted" />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  );
}
