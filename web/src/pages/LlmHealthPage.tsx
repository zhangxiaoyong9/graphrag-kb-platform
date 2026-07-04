import { useAsync } from "../hooks/useAsync";
import { getLlmHealth } from "../api/client";
import type { LlmHealthState } from "../api/types";
import { Card, CardHeader, Stat, EmptyState, Badge, Spinner } from "../components/ui";
import { IconPulse, IconRefresh, IconWarn } from "../components/icons";

const STATE_LABEL: Record<LlmHealthState, string> = {
  closed: "正常",
  open: "熔断",
  half_open: "半开",
};

const STATE_TONE: Record<LlmHealthState, "success" | "danger" | "warning"> = {
  closed: "success",
  open: "danger",
  half_open: "warning",
};

function ms(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v)} ms`;
}

/** Per-provider circuit-breaker state + gateway metrics, from GET /llm/health.
 * Reflects the API server process only (not the worker); resets on restart. */
export default function LlmHealthPage() {
  const { data, loading, error, reload } = useAsync(() => getLlmHealth(), []);
  const profiles = data?.profiles ?? [];
  const m = data?.metrics;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="LLM 健康"
          subtitle="API server 进程的 provider 熔断状态与网关指标"
          icon={<IconPulse width={18} height={18} />}
          actions={
            <button type="button" className="btn btn-ghost btn-sm" onClick={reload} aria-label="刷新">
              <IconRefresh width={16} height={16} /> 刷新
            </button>
          }
        />
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2 text-[12px] text-[#b26b00]">
          <IconWarn width={15} height={15} className="mt-0.5 shrink-0" />
          <span>仅反映 API server 进程（查询路径）的熔断器与网关指标；worker 索引路径不在此列；进程重启后数据清零。</span>
        </div>
      </Card>

      {error ? (
        <Card>
          <div className="flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            <IconWarn width={16} height={16} className="shrink-0" />
            <span>加载失败：{error}</span>
            <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={reload} aria-label="重试">
              重试
            </button>
          </div>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="TTFT p50" value={ms(m?.ttft_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移检测 p50" value={ms(m?.failover_detect_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移恢复 p50" value={ms(m?.failover_recover_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移次数" value={m?.failovers ?? 0} icon={<IconPulse width={16} height={16} />} />
            <Stat label="成功次数" value={m?.successes ?? 0} icon={<IconPulse width={16} height={16} />} />
          </div>

          <Card>
            <CardHeader title="熔断端点" subtitle="每个 provider endpoint 的当前熔断状态" icon={<IconPulse width={18} height={18} />} />
            <div className="mt-4">
              {loading && !data ? (
                <div className="flex items-center justify-center py-10 text-muted">
                  <Spinner /> <span className="ml-2 text-[13px]">加载中…</span>
                </div>
              ) : profiles.length === 0 ? (
                <EmptyState
                  icon={<IconPulse />}
                  title="暂无数据"
                  hint="尚未发起任何 LLM 调用，或服务刚重启。触发一次查询后再刷新。"
                />
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-[12px] text-muted">
                    <tr>
                      <th className="py-2">provider</th>
                      <th>model</th>
                      <th>api_base</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profiles.map((p, i) => (
                      <tr key={`${p.provider}-${p.model}-${i}`} className="border-t border-line">
                        <td className="py-2 font-mono text-[12px] text-ink">{p.provider}</td>
                        <td className="font-mono text-[12px]">{p.model}</td>
                        <td className="font-mono text-[12px] text-muted">{p.api_base ?? "—"}</td>
                        <td>
                          <Badge tone={STATE_TONE[p.state]} dot>
                            {STATE_LABEL[p.state]}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
