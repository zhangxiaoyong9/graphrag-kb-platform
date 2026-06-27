import type { EvidenceDetail } from "../api/types";
import { Button, Skeleton, Badge } from "./ui";
import { IconDoc } from "./icons";

export interface EvidenceDrawerProps {
  open: boolean;
  loading: boolean;
  evidence: EvidenceDetail | null;
  error: string | null;
  onClose: () => void;
}

export function EvidenceDrawer({ open, loading, evidence, error, onClose }: EvidenceDrawerProps) {
  if (!open) return null;

  return (
    <aside
      aria-label="证据详情"
      className="rounded-2xl border border-line bg-surface p-4 shadow-sm lg:sticky lg:top-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">证据详情</h3>
          <p className="mt-0.5 text-xs text-muted">命中片段 + 前后一小段上下文</p>
        </div>
        <Button size="sm" variant="ghost" aria-label="关闭证据抽屉" onClick={onClose}>
          关闭
        </Button>
      </div>

      <div className="mt-4 space-y-3">
        {loading ? (
          <div className="space-y-2 text-[13px] text-muted">
            <p>加载证据…</p>
            <Skeleton />
            <Skeleton className="w-5/6" />
            <Skeleton className="w-2/3" />
          </div>
        ) : error ? (
          <div className="rounded-xl border border-danger/20 bg-danger/5 p-3">
            <p className="text-sm font-medium text-danger">证据加载失败</p>
            <p className="mt-1 break-words text-xs text-muted">{error}</p>
          </div>
        ) : evidence ? (
          <EvidenceContent evidence={evidence} />
        ) : (
          <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
            选择一条引用查看证据。
          </p>
        )}
      </div>
    </aside>
  );
}

function EvidenceContent({ evidence }: { evidence: EvidenceDetail }) {
  return (
    <>
      <EvidenceBlock title="前文" empty="前文不可用" text={evidence.before} muted />
      <EvidenceBlock title="命中片段" text={evidence.matched} />
      <EvidenceBlock title="后文" empty="后文不可用" text={evidence.after} muted />
      <div className="rounded-xl border border-line bg-surface-2/60 p-3">
        <div className="mb-2 flex items-center gap-2 text-[13px] font-medium text-body">
          <IconDoc width={15} height={15} /> 来源信息
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{evidence.source.document_title}</Badge>
          <Badge tone="info">chunk {evidence.source.chunk_id}</Badge>
          <Badge tone="neutral">#{evidence.source.ordinal + 1}</Badge>
        </div>
      </div>
    </>
  );
}

function EvidenceBlock({
  title,
  text,
  empty,
  muted = false,
}: {
  title: string;
  text: string | null;
  empty?: string;
  muted?: boolean;
}) {
  return (
    <section className="rounded-xl border border-line bg-surface p-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      {text ? (
        <p className={muted ? "whitespace-pre-wrap text-[13px] leading-6 text-body" : "whitespace-pre-wrap text-sm leading-6 text-ink"}>
          {text}
        </p>
      ) : (
        <p className="text-[13px] text-muted">{empty ?? "上下文不可用"}</p>
      )}
    </section>
  );
}

export default EvidenceDrawer;
