import { useState } from "react";
import { useAsync } from "../hooks/useAsync";
import { loadAllJobs } from "../lib/aggregate";
import { cn } from "../lib/cn";
import { Card, CardHeader, Stat } from "../components/ui";
import { JobList } from "../components/JobList";
import { IconTask } from "../components/icons";

const FILTERS = [
  { key: "", label: "全部" },
  { key: "running", label: "运行中" },
  { key: "pending", label: "待处理" },
  { key: "partially_failed", label: "部分失败" },
  { key: "succeeded", label: "成功" },
  { key: "failed", label: "失败" },
];

/** Global task management: every job across all KBs, with status filters. */
export default function JobsPage() {
  const jobs = useAsync(() => loadAllJobs(), []);
  const [filter, setFilter] = useState("");
  const all = jobs.data ?? [];
  const filtered = filter ? all.filter((j) => j.status === filter) : all;
  const running = all.filter((j) => j.status === "running" || j.status === "pending").length;
  const failed = all.filter((j) => j.status === "failed" || j.status === "partially_failed").length;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-3 gap-4">
        <Stat label="任务总数" value={all.length} icon={<IconTask width={18} height={18} />} />
        <Stat label="进行中" value={running} icon={<IconTask width={18} height={18} />} />
        <Stat label="失败 / 部分失败" value={failed} icon={<IconTask width={18} height={18} />} />
      </div>

      <Card>
        <CardHeader title="全部任务" subtitle="跨所有知识库的索引任务，点击进入详情" icon={<IconTask width={18} height={18} />} />
        <div className="mt-4 flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className={cn(
                "rounded-full border px-3 py-1 text-[13px] transition-colors",
                filter === f.key
                  ? "border-brand bg-brand text-white"
                  : "border-line-strong bg-surface text-body hover:bg-surface-2",
              )}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="mt-4">
          <JobList rows={filtered} emptyHint={filter ? "该状态下没有任务" : "还没有任务"} />
        </div>
      </Card>
    </div>
  );
}
