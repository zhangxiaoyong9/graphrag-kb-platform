import { useKb } from "./kb-context";
import { useAsync } from "../hooks/useAsync";
import { listDocuments, listJobsByKb, getKbCost } from "../api/client";
import { Card, CardHeader, Stat } from "../components/ui";
import { CostPanel } from "../components/CostPanel";
import { JobList } from "../components/JobList";
import { TriggerButtons, ExportButtons } from "../components/kb-actions";
import { moneyCompact } from "../lib/format";
import { IconDoc, IconTask, IconCost, IconLayers, IconPlay } from "../components/icons";

/** KB summary tab: stats, quick actions, cumulative cost, recent jobs. */
export default function KbOverviewPage() {
  const { kbId, kb } = useKb();
  const docs = useAsync(() => listDocuments(kbId), [kbId]);
  const jobs = useAsync(() => listJobsByKb(kbId), [kbId]);
  const cost = useAsync(() => getKbCost(kbId).catch(() => null), [kbId]);

  const docCount = docs.data?.length ?? 0;
  const jobCount = jobs.data?.length ?? 0;
  const running = jobs.data?.some((j) => j.status === "running" || j.status === "pending") ?? false;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="文档数" value={docCount} icon={<IconDoc width={18} height={18} />} />
        <Stat label="任务数" value={jobCount} sub={running ? "有进行中" : "全部已结束"} icon={<IconTask width={18} height={18} />} />
        <Stat label="累计成本" value={moneyCompact(cost.data?.total_usd ?? null)} icon={<IconCost width={18} height={18} />} accent />
        <Stat label="索引方法" value={kb?.method ?? "—"} icon={<IconLayers width={18} height={18} />} />
      </div>

      <Card>
        <CardHeader title="快捷操作" subtitle="触发索引任务或导出已构建的知识图谱" icon={<IconPlay width={18} height={18} />} />
        <div className="mt-4 flex flex-wrap items-center gap-4">
          {kb && <TriggerButtons kb={kb} onTriggered={jobs.reload} />}
          <span className="text-muted">·</span>
          <ExportButtons kbId={kbId} />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="累计成本" subtitle="按步骤拆分（来源：每次 LLM 调用）" icon={<IconCost width={18} height={18} />} />
          <div className="mt-4">
            {cost.data ? (
              <CostPanel totalUsd={cost.data.total_usd} byStep={cost.data.by_step} />
            ) : (
              <p className="text-[13px] text-muted">暂无成本数据。</p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="最近任务"
            subtitle="点击进入任务详情查看步骤时间线与 unit 重试"
            icon={<IconTask width={18} height={18} />}
          />
          <div className="mt-4">
            <JobList
              rows={(jobs.data ?? []).map((j) => ({ kbId, id: j.id, status: j.status }))}
              emptyHint="还没有任务，先触发一次索引。"
            />
          </div>
        </Card>
      </div>
    </div>
  );
}
