import { useKb } from "./kb-context";
import { useAsync } from "../hooks/useAsync";
import { listJobsByKb } from "../api/client";
import { Card, CardHeader, Button } from "../components/ui";
import { JobList } from "../components/JobList";
import { TriggerButtons } from "../components/kb-actions";
import { IconTask, IconRefresh } from "../components/icons";

/** Jobs tab: trigger full/incremental + the per-KB job list. */
export default function KbJobsPage() {
  const { kbId, kb } = useKb();
  const jobs = useAsync(() => listJobsByKb(kbId), [kbId]);

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title="触发索引" subtitle="全量重建图谱，或仅处理新增/变更文档" icon={<IconTask width={18} height={18} />}
          actions={<Button variant="ghost" size="sm" onClick={jobs.reload}><IconRefresh width={15} height={15} />刷新</Button>}
        />
        <div className="mt-4">
          {kb && <TriggerButtons kb={kb} onTriggered={jobs.reload} />}
        </div>
      </Card>

      <Card>
        <CardHeader title="任务列表" subtitle="进入任务详情可查看步骤时间线与失败 unit 重试" icon={<IconTask width={18} height={18} />} />
        <div className="mt-4">
          <JobList
            rows={(jobs.data ?? []).map((j) => ({ kbId, id: j.id, status: j.status }))}
            emptyHint="还没有任务——上方触发一次全量或增量索引。"
          />
        </div>
      </Card>
    </div>
  );
}
