interface Props {
  totalUsd: number | null;
  byStep: Record<string, number>;
}

/** Cumulative/per-job cost: total USD + a bar per step, scaled to the max step. */
export function CostPanel({ totalUsd, byStep }: Props) {
  const entries = Object.entries(byStep).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? Math.max(...entries.map(([, v]) => v)) : 0;
  return (
    <div className="cost-panel space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] text-muted">累计成本</span>
        <span className="cost-total nums text-2xl font-semibold text-ink">
          {totalUsd == null ? "—" : `$${totalUsd.toFixed(4)}`}
        </span>
      </div>
      {entries.map(([step, usd]) => (
        <div key={step} className="cost-row">
          <div className="mb-1 flex items-center justify-between text-[13px]">
            <span className="cost-step text-body">{step}</span>
            <span className="cost-val nums text-muted">${usd.toFixed(4)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
            <span
              className="cost-bar block h-full rounded-full bg-brand-grad"
              style={{ width: `${max ? (usd / max) * 100 : 0}%` }}
            />
          </div>
        </div>
      ))}
      {entries.length === 0 && totalUsd != null && (
        <p className="text-[13px] text-muted">暂无按步骤拆分的成本数据。</p>
      )}
    </div>
  );
}
