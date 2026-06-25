import { useKb } from "./kb-context";
import { useAsync } from "../hooks/useAsync";
import { getKbCost } from "../api/client";
import { Card, CardHeader, Stat } from "../components/ui";
import { CostPanel } from "../components/CostPanel";
import { money, moneyCompact } from "../lib/format";
import { IconCost, IconCpu } from "../components/icons";

/** Cost tab: cumulative total, per-step bars, per-model + per-job breakdown. */
export default function KbCostPage() {
  const { kbId } = useKb();
  const cost = useAsync(() => getKbCost(kbId).catch(() => null), [kbId]);
  const data = cost.data;
  const models = data ? Object.values(data.by_model) : [];
  const jobs = data ? Object.entries(data.by_job).sort((a, b) => b[1] - a[1]) : [];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <Stat label="累计成本" value={moneyCompact(data?.total_usd ?? null)} accent icon={<IconCost width={18} height={18} />} />
        <Stat label="涉及模型" value={models.length} icon={<IconCpu width={18} height={18} />} />
        <Stat label="涉及任务" value={jobs.length} icon={<IconCost width={18} height={18} />} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="按步骤" icon={<IconCost width={18} height={18} />} />
          <div className="mt-4">
            {data ? <CostPanel totalUsd={data.total_usd} byStep={data.by_step} /> : <p className="text-[13px] text-muted">暂无数据。</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="按模型" subtitle="prompt / completion token 与美元" icon={<IconCpu width={18} height={18} />} />
          <div className="mt-4">
            {models.length === 0 ? (
              <p className="text-[13px] text-muted">暂无按模型拆分的成本。</p>
            ) : (
              <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
                {models.map((m) => (
                  <li key={m.model} className="flex items-center justify-between px-4 py-2.5">
                    <div>
                      <p className="font-mono text-[13px] text-ink">{m.model}</p>
                      <p className="nums text-[11px] text-muted">
                        {m.prompt_tokens.toLocaleString()} prompt · {m.completion_tokens.toLocaleString()} completion
                      </p>
                    </div>
                    <span className="nums text-sm font-medium text-ink">{money(m.usd)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="按任务" subtitle="每个任务的累计美元" icon={<IconCost width={18} height={18} />} />
        <div className="mt-4">
          {jobs.length === 0 ? (
            <p className="text-[13px] text-muted">暂无按任务拆分的成本。</p>
          ) : (
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
              {jobs.map(([jid, usd]) => (
                <li key={jid} className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-sm text-ink nums">任务 #{jid}</span>
                  <span className="nums text-sm font-medium text-ink">{money(usd)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  );
}
