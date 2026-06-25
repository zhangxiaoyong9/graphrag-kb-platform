import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useJobPolling } from "../hooks/useJobPolling";
import StepTimeline from "../components/StepTimeline";
import UnitTable from "../components/UnitTable";
import StatusBadge from "../components/StatusBadge";
import { CostPanel } from "../components/CostPanel";
import { Card, CardHeader, Button, ProgressBar } from "../components/ui";
import { retryStep, getJobCost } from "../api/client";
import type { JobCost } from "../api/types";
import { pct } from "../lib/format";
import { IconArrowLeft, IconTask, IconRefresh, IconCost } from "../components/icons";

/** Task detail: step timeline + per-step/unit progress + retry, with polling. */
export default function JobDetailPage() {
  const { id, jobId } = useParams();
  const kbId = Number(id);
  const jobIdNum = Number(jobId);
  const job = useJobPolling(jobIdNum);
  const [selected, setSelected] = useState<number | null>(null);
  const [cost, setCost] = useState<JobCost | null>(null);
  const status = job?.status;

  // Fetch cost alongside the poll; stop once terminal (matches useJobPolling).
  useEffect(() => {
    if (!kbId || !jobIdNum) return;
    let stop = false;
    const tick = () =>
      getJobCost(kbId, jobIdNum)
        .then((c) => {
          if (!stop) setCost(c);
        })
        .catch(() => {});
    tick();
    if (status && ["succeeded", "failed", "cancelled"].includes(status)) return;
    const h = setInterval(tick, 2000);
    return () => {
      stop = true;
      clearInterval(h);
    };
  }, [kbId, jobIdNum, status]);

  if (!job) return <div className="card card-pad text-sm text-muted">加载任务中…</div>;

  const step = job.steps.find((s) => s.id === selected) ?? null;

  // Aggregate overall progress across steps that carry unit progress.
  const agg = job.steps.reduce(
    (acc, s) => {
      if (s.progress) {
        acc.ok += s.progress.succeeded;
        acc.fail += s.progress.failed;
        acc.total += s.progress.total;
      }
      return acc;
    },
    { ok: 0, fail: 0, total: 0 },
  );
  const overall = pct(agg.ok, agg.total);

  return (
    <div className="space-y-5">
      <Link
        to={`/kbs/${kbId}/jobs`}
        className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink"
      >
        <IconArrowLeft width={14} height={14} /> 任务列表
      </Link>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-grad-soft text-brand">
              <IconTask width={20} height={20} />
            </span>
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                任务 #{job.id} <StatusBadge status={job.status} />
              </h1>
              <p className="text-[12px] text-muted nums">共 {job.steps.length} 个步骤</p>
            </div>
          </div>
          <div className="min-w-[180px] flex-1 sm:max-w-xs">
            <div className="mb-1 flex justify-between text-[12px] text-muted">
              <span>总进度</span>
              <span className="nums">
                {agg.ok}/{agg.total} 成功{agg.fail ? ` · ${agg.fail} 失败` : ""}
              </span>
            </div>
            <ProgressBar value={overall ?? 0} tone={agg.fail ? "warning" : "brand"} />
          </div>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-5">
        <div className="space-y-5 lg:col-span-2">
          <Card>
            <CardHeader title="步骤时间线" icon={<IconTask width={18} height={18} />} />
            <div className="mt-4">
              <StepTimeline steps={job.steps} selected={selected} onSelect={setSelected} />
            </div>
          </Card>
          {cost && (
            <Card>
              <CardHeader title="任务成本" icon={<IconCost width={18} height={18} />} />
              <div className="mt-4">
                <CostPanel totalUsd={cost.total_usd} byStep={cost.by_step} />
              </div>
            </Card>
          )}
        </div>

        <div className="lg:col-span-3">
          <Card>
            <CardHeader
              title={step ? step.name : "选择一个步骤"}
              subtitle={
                step
                  ? `${step.kind} · 共 ${step.progress?.total ?? 0} 个 unit`
                  : "在左侧时间线点击某一步骤查看 unit"
              }
              icon={<IconTask width={18} height={18} />}
              actions={
                step && step.status === "partially_failed" ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={async () => {
                      await retryStep(step.id);
                    }}
                  >
                    <IconRefresh width={14} height={14} /> 重试失败 unit
                  </Button>
                ) : undefined
              }
            />
            <div className="mt-4">
              {step ? (
                <UnitTable stepId={step.id} active={job.status === "running"} />
              ) : (
                <p className="rounded-xl border border-dashed border-line-strong px-4 py-10 text-center text-[13px] text-muted">
                  点击左侧任一步骤查看其 unit 列表与重试
                </p>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
