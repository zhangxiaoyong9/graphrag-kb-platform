import { useState } from "react";
import { Link } from "react-router-dom";
import { addDocument, uploadFile, deleteDocument } from "../api/client";
import type { DocumentOut } from "../api/types";
import { humanBytes } from "../lib/format";
import { statusLabel, statusTone } from "../lib/status";
import { Button, EmptyState, Field } from "./ui";
import { IconDoc, IconTrash, IconUpload, IconPlus } from "./icons";

/** Document list + multipart upload + paste + delete for one KB. */
export function DocumentManager({
  kbId,
  docs,
  reload,
}: {
  kbId: number;
  docs: DocumentOut[];
  reload: () => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pasteBusy, setPasteBusy] = useState(false);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await uploadFile(kbId, file);
      reload();
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  };

  const onDelete = async (doc: DocumentOut) => {
    const ok = window.confirm(
      `确定删除文档「${doc.title}」吗？\n\n删除后图谱不会自动回缩——需要重新运行增量任务来刷新索引。`,
    );
    if (!ok) return;
    await deleteDocument(kbId, doc.id);
    reload();
  };

  return (
    <div className="space-y-4">
      {docs.length === 0 ? (
        <EmptyState
          icon={<IconDoc />}
          title="还没有文档"
          hint="上传文件（PDF / Word / Markdown / 纯文本）或直接粘贴文本，开始构建知识图谱。"
        />
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {docs.map((d) => {
            const tone = statusTone(d.status);
            const toneCls = {
              success: "text-success",
              danger: "text-danger",
              warning: "text-[#b26b00]",
              info: "text-info",
              neutral: "text-muted",
              brand: "text-brand",
            }[tone];
            return (
              <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-brand">
                    <IconDoc width={18} height={18} />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{d.title}</p>
                    <p className="mt-0.5 text-xs text-muted nums">
                      {humanBytes(d.bytes)} · {d.chunk_count} 个分块 ·{" "}
                      <span className={toneCls}>{statusLabel(d.status)}</span>
                    </p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Link to={`/kbs/${kbId}/documents/${d.id}`} className="btn btn-sm btn-secondary" aria-label={`查看文档 ${d.title}`}>
                    查看
                  </Link>
                  <button
                    onClick={() => onDelete(d)}
                    className="btn btn-sm btn-danger"
                    aria-label={`删除文档 ${d.title}`}
                  >
                    <IconTrash width={14} height={14} />
                    删除
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-dashed border-line-strong bg-surface-2/50 p-4">
          <p className="mb-2 flex items-center gap-2 text-[13px] font-medium text-body">
            <IconUpload width={15} height={15} /> 上传文件
          </p>
          <label className="block cursor-pointer">
            <input
              aria-label="上传文件"
              type="file"
              disabled={busy}
              onChange={onFile}
              className="block w-full text-[13px] text-muted file:mr-3 file:rounded-lg file:border-0 file:bg-brand file:px-3 file:py-2 file:text-white file:hover:bg-brand-600"
            />
            <p className="mt-2 text-xs text-muted">
              支持 .txt / .md / .pdf / .docx / .html 等，单文件 ≤ 25 MiB
            </p>
          </label>
        </div>

        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setPasteBusy(true);
            try {
              await addDocument(kbId, { title: title || "untitled", text });
              setTitle("");
              setText("");
              reload();
            } finally {
              setPasteBusy(false);
            }
          }}
          className="space-y-2 rounded-xl border border-dashed border-line-strong bg-surface-2/50 p-4"
        >
          <p className="flex items-center gap-2 text-[13px] font-medium text-body">
            <IconPlus width={15} height={15} /> 粘贴文本
          </p>
          <input
            className="input"
            placeholder="标题（可选）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            className="textarea h-20"
            placeholder="在此粘贴正文内容…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" variant="primary" size="sm" disabled={pasteBusy || !text}>
            {pasteBusy ? "添加中…" : "添加文档"}
          </Button>
        </form>
      </div>
    </div>
  );
}

export default DocumentManager;
