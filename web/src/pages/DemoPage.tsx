import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardHeader, Stat, Badge } from "../components/ui";
import { CostPanel } from "../components/CostPanel";
import { JobList } from "../components/JobList";
import StepTimeline from "../components/StepTimeline";
import StatusBadge from "../components/StatusBadge";
import { short } from "../lib/format";
import {
  demoKbs,
  demoDocs,
  demoJob,
  demoSteps,
  demoUnits,
  demoKbCost,
  demoJobCost,
  demoGraph,
  demoQuery,
  demoHealth,
} from "../mock/demo";

/**
 * Preview page: renders the key UI surfaces against the CENTRALIZED mock data in
 * `src/mock/demo.ts`, so the design can be reviewed/screenshotted without the
 * Python backend or worker running. Not wired to any live data.
 */
export default function DemoPage() {
  const [selected, setSelected] = useState<number | null>(demoSteps[2]?.id ?? null);

  // Circular layout for a canvas-free SVG graph preview.
  const cx = 150, cy = 150, R = 110;
  const pos = (i: number) => {
    const a = (i / demoGraph.nodes.length) * Math.PI * 2 - Math.PI / 2;
    return { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) };
  };
  const idx = (id: string) => demoGraph.nodes.findIndex((n) => n.id === id);
  const hue = (c: string) => {
    let h = 0;
    for (let i = 0; i < c.length; i++) h = (h * 31 + c.charCodeAt(i)) % 360;
    return h;
  };

  const jobRows = demoKbs.map((k, i) => ({
    kbId: k.id,
    kbName: k.name,
    id: 10 - i,
    status: ["running", "partially_failed", "succeeded"][i] ?? "succeeded",
  }));

  return (
    <div className="space-y-5">
      <div className="card flex items-center gap-3 border-warning/30 bg-warning-soft/40 p-4">
        <Badge tone="warning">演示数据</Badge>
        <p className="text-[13px] text-body">
          本页使用 <code className="rounded bg-surface px-1 font-mono text-[12px]">src/mock/demo.ts</code> 中的集中管理样本数据，用于预览 UI，未连接真实后端。
          <Link to="/" className="ml-2 text-brand hover:underline">返回概览 →</Link>
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="知识库" value={demoKbs.length} />
        <Stat label="文档" value={demoDocs.length} />
        <Stat label="累计成本" value={`$${(demoKbCost.total_usd ?? 0).toFixed(4)}`} accent />
        <Stat label="系统状态" value={demoHealth.status === "ok" ? "正常" : "降级"} />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="步骤时间线（示例任务）" subtitle={`任务 #${demoJob.id}`} />
          <div className="mt-4">
            <StepTimeline steps={demoSteps} selected={selected} onSelect={setSelected} />
          </div>
        </Card>
        <Card>
          <CardHeader title="累计成本" />
          <div className="mt-4">
            <CostPanel totalUsd={demoKbCost.total_usd} byStep={demoKbCost.by_step} />
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="任务成本示例" />
          <div className="mt-4">
            <CostPanel totalUsd={demoJobCost.total_usd} byStep={demoJobCost.by_step} />
          </div>
        </Card>
        <Card>
          <CardHeader title="Unit 列表示例" subtitle="每个分块/实体/社区的执行单元" />
          <div className="mt-4 overflow-hidden rounded-xl border border-line">
            <table className="w-full text-sm">
              <tbody className="divide-y divide-line">
                {demoUnits.map((u) => (
                  <tr key={u.id}>
                    <td className="px-3 py-2 font-mono text-[12px]">{short(u.subject_id)}</td>
                    <td className="px-3 py-2"><StatusBadge status={u.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="任务列表示例" />
        <div className="mt-4">
          <JobList rows={jobRows} />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="图谱可视化预览" subtitle="静态 SVG 预览（实际页面为可交互力导向图）" />
          <div className="mt-4 rounded-xl border border-line bg-surface p-2">
            <svg viewBox="0 0 300 300" className="h-[300px] w-full">
              {demoGraph.edges.map((e, i) => {
                const a = pos(idx(e.source));
                const b = pos(idx(e.target));
                return (
                  <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#c9cefb" strokeWidth={Math.max(1, e.weight / 2)} />
                );
              })}
              {demoGraph.nodes.map((n, i) => {
                const p = pos(i);
                const color = `hsl(${hue(n.community ?? "")},70%,55%)`;
                return (
                  <g key={n.id}>
                    <circle cx={p.x} cy={p.y} r={Math.max(7, n.degree / 1.5)} fill={color} opacity={0.9} />
                    <text x={p.x} y={p.y - Math.max(7, n.degree / 1.5) - 3} textAnchor="middle" className="fill-ink text-[9px]">
                      {n.title}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </Card>
        <Card>
          <CardHeader title="检索问答示例" subtitle={`方法 · ${demoQuery.method}`} />
          <div className="mt-4 whitespace-pre-wrap rounded-xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink">
            {demoQuery.answer}
          </div>
        </Card>
      </div>
    </div>
  );
}
