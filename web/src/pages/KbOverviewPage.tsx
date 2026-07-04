import { useState } from "react";
import { useKb } from "./kb-context";
import { useAsync } from "../hooks/useAsync";
import { listDocuments, listJobsByKb, getKbCost, getKbStats } from "../api/client";
import { Badge, Button, Card, CardHeader, Stat } from "../components/ui";
import { CostPanel } from "../components/CostPanel";
import { JobList } from "../components/JobList";
import KbForm from "../components/KbForm";
import { TriggerButtons, ExportButtons } from "../components/kb-actions";
import { moneyCompact } from "../lib/format";
import { IconDoc, IconTask, IconCost, IconLayers, IconPlay, IconGear } from "../components/icons";

/** KB summary tab: stats, quick actions, cumulative cost, recent jobs. */
export default function KbOverviewPage() {
  const { kbId, kb, reload } = useKb();
  const docs = useAsync(() => listDocuments(kbId), [kbId]);
  const jobs = useAsync(() => listJobsByKb(kbId), [kbId]);
  const cost = useAsync(() => getKbCost(kbId).catch(() => null), [kbId]);
  const stats = useAsync(() => getKbStats(kbId).catch(() => null), [kbId]);
  const s = stats.data;
  const dash = "—";
  const [editOpen, setEditOpen] = useState(false);

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

      <div className="text-[12px] text-muted">
        数据目录：<span className="font-mono text-ink/70">{kb?.data_root ?? "—"}</span>
      </div>

      <Card>
        <CardHeader title="图谱规模" subtitle="最近一次索引后的实体 / 关系 / 社区计数" icon={<IconLayers width={18} height={18} />} />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="实体" value={s?.entity_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="关系" value={s?.relationship_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="社区" value={s?.community_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="社区报告" value={s?.community_report_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="分块" value={s?.chunk_count ?? dash} icon={<IconDoc width={16} height={16} />} />
        </div>
      </Card>

      <Card>
        <CardHeader title="快捷操作" subtitle="触发索引任务或导出已构建的知识图谱" icon={<IconPlay width={18} height={18} />} />
        <div className="mt-4 flex flex-wrap items-center gap-4">
          {kb && <TriggerButtons kb={kb} onTriggered={jobs.reload} />}
          <span className="text-muted">·</span>
          <ExportButtons kbId={kbId} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="模型配置"
          subtitle="创建知识库时通过 settings_yaml 设定（密钥不入库）"
          icon={<IconLayers width={18} height={18} />}
          actions={
            kb && (
              <Button variant="secondary" size="sm" onClick={() => setEditOpen(true)}>
                <IconGear width={15} height={15} />
                编辑配置
              </Button>
            )
          }
        />
        <div className="mt-4">
          <ModelConfig settings={kb?.settings} />
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

      {editOpen && kb && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-4 backdrop-blur-sm"
          onClick={() => setEditOpen(false)}
        >
          <div
            className="card my-8 w-full max-w-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-3">
              <h3 className="text-[15px] font-semibold">编辑配置</h3>
              <button
                className="text-muted hover:text-ink"
                onClick={() => setEditOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="p-5">
              <KbForm
                kb={kb}
                onSaved={() => {
                  setEditOpen(false);
                  reload();
                }}
              />
              <p className="mt-3 text-[12px] text-muted">
                提示：配置已更新，需重新索引才生效。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ModelConfig({ settings }: { settings: Record<string, unknown> | undefined }) {
  if (!settings) return <p className="text-[13px] text-muted">未读取到配置。</p>;
  const llm = (settings.llm as Record<string, unknown> | undefined) ?? {};
  const emb = (settings.embedding as Record<string, unknown> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, unknown> | undefined) ?? {};
  const rows: { k: string; v: string }[] = [
    { k: "LLM provider", v: String(llm.model_provider ?? "—") },
    { k: "LLM model", v: String(llm.model ?? "—") },
    { k: "Embedding model", v: String(emb.model ?? "—") },
  ];
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-3">
        {rows.map((r) => (
          <div key={r.k} className="rounded-lg border border-line bg-surface-2/40 px-3 py-2">
            <p className="text-[11px] text-muted">{r.k}</p>
            <p className="mt-0.5 truncate font-mono text-[13px] text-ink">{r.v}</p>
          </div>
        ))}
      </div>
      <p className="text-[12px] text-muted">
        社区报告结构化输出：
        <Badge tone={cr.structured_output === false ? "warning" : "info"} className="ml-1">
          {cr.structured_output === false ? "关闭（纯文本回退）" : "开启（json_schema）"}
        </Badge>
      </p>
    </div>
  );
}
