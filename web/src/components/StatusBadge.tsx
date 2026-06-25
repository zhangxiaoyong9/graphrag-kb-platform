import { Badge } from "./ui";
import { statusTone, statusLabel } from "../lib/status";

/** Status pill with a Chinese label and a pulsing dot while running. */
export default function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <Badge tone={tone} dot={status === "running"} className={status === "running" ? "animate-pulse-ring" : undefined}>
      {statusLabel(status)}
    </Badge>
  );
}
