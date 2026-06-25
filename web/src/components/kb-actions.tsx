import { useState } from "react";
import type { KbOut } from "../api/types";
import { triggerJob } from "../api/client";
import { Button } from "./ui";
import { IconDownload, IconPlay, IconRefresh } from "./icons";

/** Export anchors — browser-native download via the backend stream endpoint. */
export function ExportButtons({ kbId }: { kbId: number }) {
  return (
    <div className="flex flex-wrap gap-2">
      <a className="btn btn-secondary btn-sm" href={`/kbs/${kbId}/export?format=zip`} download>
        <IconDownload width={15} height={15} /> 导出 zip
      </a>
      <a className="btn btn-secondary btn-sm" href={`/kbs/${kbId}/export?format=graphml`} download>
        <IconDownload width={15} height={15} /> GraphML
      </a>
    </div>
  );
}

/** Trigger full / incremental indexing; uses the KB's configured method. */
export function TriggerButtons({
  kb,
  onTriggered,
}: {
  kb: Pick<KbOut, "id" | "method">;
  onTriggered?: () => void;
}) {
  const [busy, setBusy] = useState<"full" | "incremental" | null>(null);
  const [last, setLast] = useState<string | null>(null);

  const run = async (type: "full" | "incremental") => {
    setBusy(type);
    try {
      const r = await triggerJob(kb.id, kb.method, type);
      setLast(`已创建任务 #${r.id}`);
      onTriggered?.();
    } catch (e) {
      setLast(`触发失败：${(e as Error).message ?? e}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary" size="sm" disabled={busy !== null} onClick={() => run("full")}>
        <IconPlay width={15} height={15} />
        {busy === "full" ? "提交中…" : "全量索引"}
      </Button>
      <Button variant="secondary" size="sm" disabled={busy !== null} onClick={() => run("incremental")}>
        <IconRefresh width={15} height={15} />
        {busy === "incremental" ? "提交中…" : "增量索引"}
      </Button>
      {last && <span className="text-[12px] text-muted">{last}</span>}
    </div>
  );
}
