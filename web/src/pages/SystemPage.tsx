import { useAsync } from "../hooks/useAsync";
import { getHealth } from "../api/client";
import { Card, CardHeader, Badge } from "../components/ui";
import { statusLabel, statusTone } from "../lib/status";
import { relTime } from "../lib/format";
import { IconSystem, IconCpu, IconDatabase, IconPulse } from "../components/icons";

const ENDPOINTS: { m: string; path: string; desc: string }[] = [
  { m: "GET", path: "/health", desc: "存活探针：数据库探活 + worker 心跳" },
  { m: "POST", path: "/kbs", desc: "创建知识库" },
  { m: "GET", path: "/kbs", desc: "获取所有知识库" },
  { m: "POST", path: "/kbs/{id}/documents", desc: "添加文档（JSON 或 multipart 文件）" },
  { m: "DELETE", path: "/kbs/{id}/documents/{doc_id}", desc: "删除文档及其分块（图不回缩）" },
  { m: "POST", path: "/kbs/{id}/jobs", desc: "触发全量 / 增量索引" },
  { m: "GET", path: "/kbs/{id}/cost", desc: "累计成本（按 step / model / job）" },
  { m: "GET", path: "/kbs/{id}/export?format=zip|graphml", desc: "导出索引" },
  { m: "GET", path: "/kbs/{id}/graph?limit=&q=&hop=", desc: "图可视化数据" },
  { m: "POST", path: "/kbs/{id}/query", desc: "检索问答（local/global/drift/basic）" },
  { m: "POST", path: "/units/{id}/retry", desc: "重试单个失败 unit" },
  { m: "POST", path: "/steps/{id}/retry", desc: "整步批量重试失败 unit" },
];

/** System status + architecture + API reference. */
export default function SystemPage() {
  const health = useAsync(() => getHealth().catch(() => null), []);
  const h = health.data;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatusCard
          label="总体状态"
          value={h ? statusLabel(h.status) : "离线"}
          tone={statusTone(h?.status)}
          icon={<IconSystem width={18} height={18} />}
        />
        <StatusCard
          label="数据库（控制面）"
          value={h ? (h.db === "ok" ? "正常" : "异常") : "—"}
          tone={h?.db === "ok" ? "success" : "danger"}
          icon={<IconDatabase width={18} height={18} />}
        />
        <StatusCard
          label="Worker 心跳"
          value={h ? (h.worker.stale ? "过期" : "活跃") : "—"}
          tone={h ? (h.worker.stale ? "warning" : "success") : "neutral"}
          sub={h ? `最近 · ${relTime(h.worker.last_heartbeat_at)}` : undefined}
          icon={<IconPulse width={18} height={18} />}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="架构" subtitle="控制面 / 数据面分离" icon={<IconSystem width={18} height={18} />} />
          <ul className="mt-4 space-y-3 text-[13px]">
            {[
              { t: "控制面（SQLite）", d: "追踪 jobs / steps / units / documents / 重试记录" },
              { t: "数据面（parquet + LanceDB）", d: "图谱输出与向量嵌入，存于 <data_root>/vectors/" },
              { t: "Worker", d: "轮询 SQLite 执行索引引擎；崩溃自恢复；SIGTERM 优雅关闭" },
              { t: "API 服务", d: "FastAPI REST + 托管 React SPA，绝不直接运行索引" },
            ].map((x) => (
              <li key={x.t} className="flex gap-3">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" />
                <span>
                  <span className="font-medium text-ink">{x.t}</span>
                  <span className="ml-1 text-muted">— {x.d}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardHeader title="进程" subtitle="两个独立进程协同工作" icon={<IconCpu width={18} height={18} />} />
          <div className="mt-4 space-y-3">
            <div className="rounded-xl border border-line bg-surface-2/50 p-3">
              <p className="font-mono text-[12px] text-ink">python -m kb_platform.server kb.db . 127.0.0.1 8000</p>
              <p className="mt-1 text-[12px] text-muted">API 服务：REST 接口 + 前端页面</p>
            </div>
            <div className="rounded-xl border border-line bg-surface-2/50 p-3">
              <p className="font-mono text-[12px] text-ink">python -m kb_platform.worker kb.db</p>
              <p className="mt-1 text-[12px] text-muted">后台 worker：轮询 SQLite → 执行索引任务</p>
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="API 接口" subtitle="前端调用的全部 REST 端点" icon={<IconSystem width={18} height={18} />} />
        <div className="mt-4 overflow-hidden rounded-xl border border-line">
          <table className="w-full text-[13px]">
            <tbody className="divide-y divide-line">
              {ENDPOINTS.map((e) => (
                <tr key={e.m + e.path} className="hover:bg-surface-2/50">
                  <td className="px-3 py-2">
                    <Badge tone={e.m === "GET" ? "info" : "brand"}>{e.m}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px] text-ink">{e.path}</td>
                  <td className="px-3 py-2 text-muted">{e.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function StatusCard({
  label,
  value,
  tone,
  sub,
  icon,
}: {
  label: string;
  value: string;
  tone: ReturnType<typeof statusTone>;
  sub?: string;
  icon: React.ReactNode;
}) {
  const dotCls = {
    success: "bg-success",
    danger: "bg-danger",
    warning: "bg-warning",
    info: "bg-info",
    neutral: "bg-neutral",
    brand: "bg-brand",
  }[tone];
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-muted">{label}</span>
        <span className="text-muted">{icon}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${dotCls} ${value === "活跃" || value === "正常" ? "animate-pulse" : ""}`} />
        <span className="text-lg font-semibold text-ink">{value}</span>
      </div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}
