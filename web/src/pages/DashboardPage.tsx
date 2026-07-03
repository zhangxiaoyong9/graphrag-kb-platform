import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { listKbs, getHealth } from "../api/client";
import { loadAllJobs, loadAllCost } from "../lib/aggregate";
import { Card, CardHeader, Stat, Button, EmptyState } from "../components/ui";
import { JobList } from "../components/JobList";
import { moneyCompact, relTime } from "../lib/format";
import { statusLabel, statusTone } from "../lib/status";
import {
  IconDatabase,
  IconTask,
  IconCost,
  IconPulse,
  IconPlus,
  IconChevronRight,
} from "../components/icons";

/** Global overview dashboard. */
export default function DashboardPage() {
  const kbs = useAsync(() => listKbs(), []);
  const jobs = useAsync(() => loadAllJobs(), []);
  const cost = useAsync(() => loadAllCost().catch(() => null), []);
  const health = useAsync(() => getHealth().catch(() => null), []);

  const running = jobs.data?.filter((j) => j.status === "running" || j.status === "pending") ?? [];
  const tone = statusTone(health.data?.status);

  return (
    <div className="space-y-5">
      {/* Hero */}
      <div className="card relative overflow-hidden bg-brand-grad text-white">
        <div className="pointer-events-none absolute -right-10 -top-16 h-52 w-52 rounded-full bg-white/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 right-32 h-44 w-44 rounded-full bg-violet/30 blur-3xl" />
        <div className="relative flex flex-wrap items-center justify-between gap-4 p-6">
          <div>
            <p className="text-[13px] text-white/80">知识库平台 · GraphRAG</p>
            <h1 className="mt-1 text-2xl font-semibold">从非结构化文本到可检索的知识图谱</h1>
            <p className="mt-2 max-w-xl text-[13px] text-white/85">
              创建知识库、追踪每一步与每个分块、监控成本，并用 local / global / drift / basic / cypher / hybrid 六种方式检索问答。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/kbs" className="btn btn-md bg-white text-brand hover:bg-white/90">
              <IconDatabase width={16} height={16} /> 进入知识库
            </Link>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="知识库" value={kbs.data?.length ?? "—"} icon={<IconDatabase width={18} height={18} />} />
        <Stat label="任务总数" value={jobs.data?.length ?? "—"} sub={running.length ? `${running.length} 个进行中` : "全部已结束"} icon={<IconTask width={18} height={18} />} />
        <Stat label="累计成本" value={moneyCompact(cost.data?.totalUsd ?? null)} icon={<IconCost width={18} height={18} />} />
        <div className="card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-muted">系统状态</span>
            <IconPulse width={18} height={18} className={tone === "success" ? "text-success" : tone === "warning" ? "text-warning" : "text-muted"} />
          </div>
          <div className={`mt-2 text-[20px] font-semibold ${tone === "success" ? "text-success" : tone === "warning" ? "text-[#b26b00]" : "text-muted"}`}>
            {statusLabel(health.data?.status)}
          </div>
          <div className="mt-1 text-xs text-muted">
            Worker 心跳 · {health.data ? relTime(health.data.worker.last_heartbeat_at) : "—"}
          </div>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        {/* Recent jobs */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="最近任务"
            subtitle="跨所有知识库的最新索引任务"
            icon={<IconTask width={18} height={18} />}
            actions={
              <Link to="/jobs" className="text-[13px] font-medium text-brand hover:underline">
                全部
              </Link>
            }
          />
          <div className="mt-4">
            {jobs.data && jobs.data.length > 0 ? (
              <JobList rows={jobs.data.slice(0, 6)} />
            ) : (
              <EmptyState
                icon={<IconTask />}
                title="还没有任务"
                hint="在知识库详情中触发一次全量索引，任务会出现在这里。"
                action={<Link to="/kbs"><Button variant="secondary" size="sm">浏览知识库</Button></Link>}
              />
            )}
          </div>
        </Card>

        {/* KB list teaser */}
        <Card>
          <CardHeader
            title="知识库"
            icon={<IconDatabase width={18} height={18} />}
            actions={
              <Link to="/kbs" className="text-brand hover:underline">
                <IconPlus width={15} height={15} className="inline" />
              </Link>
            }
          />
          <div className="mt-3 space-y-1">
            {(kbs.data ?? []).slice(0, 6).map((k) => (
              <Link
                key={k.id}
                to={`/kbs/${k.id}`}
                className="flex items-center justify-between rounded-lg px-2 py-2 transition-colors hover:bg-surface-2"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-ink">{k.name}</span>
                  <span className="text-[11px] text-muted nums">{k.method} · #{k.id}</span>
                </span>
                <IconChevronRight width={15} height={15} className="text-muted" />
              </Link>
            ))}
            {kbs.data && kbs.data.length === 0 && (
              <p className="rounded-lg border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
                还没有知识库
              </p>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
