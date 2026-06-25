/** Centralized status -> tone + Chinese label maps. */

export type Tone = "brand" | "success" | "danger" | "warning" | "info" | "neutral";

export const STATUS_TONE: Record<string, Tone> = {
  succeeded: "success",
  success: "success",
  ready: "success",
  parsed: "success",
  ok: "success",
  failed: "danger",
  down: "danger",
  degraded: "warning",
  running: "info",
  pending: "neutral",
  queued: "neutral",
  partially_failed: "warning",
  cancelled: "neutral",
  uploaded: "neutral",
  idle: "neutral",
};

export const STATUS_LABEL_ZH: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  running: "运行中",
  pending: "待处理",
  partially_failed: "部分失败",
  cancelled: "已取消",
  uploaded: "已上传",
  ready: "就绪",
  parsed: "已解析",
  ok: "正常",
  degraded: "降级",
  down: "异常",
  idle: "空闲",
  stale: "过期",
};

export function statusTone(status: string | null | undefined): Tone {
  if (!status) return "neutral";
  return STATUS_TONE[status] ?? "neutral";
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return STATUS_LABEL_ZH[status] ?? status;
}

/** Tailwind classes for a badge of the given tone. */
export function toneClasses(tone: Tone): string {
  const map: Record<Tone, string> = {
    brand: "bg-brand-50 text-brand-700",
    success: "bg-success-soft text-success",
    danger: "bg-danger-soft text-danger",
    warning: "bg-warning-soft text-[#b26b00]",
    info: "bg-info-soft text-info",
    neutral: "bg-neutral-soft text-[#5b6478]",
  };
  return map[tone];
}

/** Whether a job/step status is terminal (no more polling). */
export function isTerminal(status: string | null | undefined): boolean {
  return !!status && ["succeeded", "failed", "cancelled"].includes(status);
}
