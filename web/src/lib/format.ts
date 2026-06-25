/** Display + formatting helpers shared across the UI. */

/** Human-readable byte size, e.g. 2048 -> "2.0 KB". */
export function humanBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / Math.pow(1024, i);
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** USD cost -> "$0.0123" (4dp), or "—" when unknown. */
export function money(usd: number | null | undefined): string {
  return usd == null ? "—" : `$${usd.toFixed(4)}`;
}

/** Compact USD for stat cards, e.g. $1.23 -> "$1.23". */
export function moneyCompact(usd: number | null | undefined): string {
  if (usd == null) return "—";
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(4)}`;
}

/** Percentage of a part over a total, rounded. Returns null when total is 0. */
export function pct(part: number, total: number): number | null {
  if (!total) return null;
  return Math.round((part / total) * 100);
}

/** Shorten a long id for table cells. */
export function short(s: string, n = 12): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

/** Chinese relative time, e.g. "刚刚", "3 分钟前", "2 小时前". */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const sec = Math.round(diff / 1000);
  if (sec < 5) return "刚刚";
  if (sec < 60) return `${sec} 秒前`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.round(hr / 24);
  return `${day} 天前`;
}

/** Translate a graphrag workflow step name to a friendly Chinese label. */
const STEP_LABELS: Record<string, string> = {
  chunk_documents: "文本分块",
  extract_graph: "图谱抽取",
  summarize_descriptions: "描述摘要",
  finalize_graph: "图谱收尾",
  create_communities: "社区聚类",
  community_reports: "社区报告",
  generate_text_embeddings: "向量嵌入",
  merge_delta: "增量合并",
  reconsolidate: "重新整合",
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name;
}
