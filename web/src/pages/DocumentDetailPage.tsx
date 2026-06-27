import { Link, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { getDocumentDetail, getDocumentEvidence } from "../api/client";
import type { DocumentDetail, EvidenceDetail } from "../api/types";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from "../components/ui";
import { IconArrowLeft, IconDoc, IconGraph } from "../components/icons";
import { humanBytes } from "../lib/format";
import { statusLabel } from "../lib/status";
import { useKb } from "./kb-context";

export default function DocumentDetailPage() {
  const { kbId } = useKb();
  const { docId } = useParams();
  const numericDocId = Number(docId);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setSelectedCitation(null);
    setEvidence(null);
    setEvidenceError(null);
    getDocumentDetail(kbId, numericDocId)
      .then((value) => {
        if (alive) setDetail(value);
      })
      .catch((err: Error) => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [kbId, numericDocId]);

  const openEvidence = (citationId: string) => {
    setSelectedCitation(citationId);
    setEvidence(null);
    setEvidenceError(null);
    setEvidenceLoading(true);
    getDocumentEvidence(kbId, numericDocId, citationId)
      .then(setEvidence)
      .catch((err: Error) => setEvidenceError(err.message))
      .finally(() => setEvidenceLoading(false));
  };

  if (loading) {
    return (
      <Card>
        <CardHeader title="文档详情" subtitle="加载文档正文与引用…" icon={<IconDoc width={18} height={18} />} />
        <div className="mt-5 space-y-3">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton />
          <Skeleton className="w-5/6" />
          <Skeleton className="w-2/3" />
        </div>
      </Card>
    );
  }

  if (error || !detail) {
    return (
      <EmptyState
        icon={<IconDoc />}
        title="文档加载失败"
        hint={error ?? "无法读取该文档。"}
        action={<Link to="../documents" className="btn btn-secondary btn-sm">返回文档列表</Link>}
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to="../documents" className="inline-flex items-center gap-1 text-[13px] font-medium text-brand hover:underline">
          <IconArrowLeft width={14} height={14} /> 返回文档列表
        </Link>
        <Link to="entities" className="btn btn-secondary btn-sm">
          <IconGraph width={14} height={14} /> 实体 / 关系
        </Link>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardHeader
            title={detail.title}
            subtitle={`${humanBytes(detail.bytes)} · ${detail.chunk_count} 个分块 · ${statusLabel(detail.status)}`}
            icon={<IconDoc width={18} height={18} />}
            actions={<Badge tone={detail.chunk_count > 0 ? "success" : "warning"}>{detail.chunk_count > 0 ? "已分块" : "待索引"}</Badge>}
          />

          <article className="mt-5 rounded-2xl border border-line bg-surface-2/40 p-4">
            <h4 className="mb-3 text-sm font-semibold text-ink">正文</h4>
            <p className="whitespace-pre-wrap text-sm leading-7 text-body">{detail.text || "该文档没有可显示正文。"}</p>
          </article>

          <section className="mt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h4 className="text-sm font-semibold text-ink">引用列表</h4>
                <p className="text-xs text-muted">点击引用后在右侧查看命中片段与上下文。</p>
              </div>
            </div>

            {detail.citations.length === 0 ? (
              <EmptyState
                icon={<IconDoc />}
                title="暂无可验证引用"
                hint="文档正文仍可阅读；引用与实体关系会在索引完成后出现。"
              />
            ) : (
              <ul className="divide-y divide-line rounded-xl border border-line">
                {detail.citations.map((citation) => (
                  <li key={citation.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink">{citation.label}</p>
                      <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted">{citation.snippet}</p>
                    </div>
                    <Button
                      size="sm"
                      variant={selectedCitation === citation.id ? "primary" : "secondary"}
                      onClick={() => openEvidence(citation.id)}
                      aria-label={`查看证据 ${citation.label}`}
                    >
                      查看证据
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </Card>

        <EvidenceDrawer
          open={selectedCitation !== null}
          loading={evidenceLoading}
          evidence={evidence}
          error={evidenceError}
          onClose={() => {
            setSelectedCitation(null);
            setEvidence(null);
            setEvidenceError(null);
          }}
        />
      </div>
    </div>
  );
}
