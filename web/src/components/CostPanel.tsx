interface Props {
  totalUsd: number | null;
  byStep: Record<string, number>;
}

export function CostPanel({ totalUsd, byStep }: Props) {
  const entries = Object.entries(byStep).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? Math.max(...entries.map(([, v]) => v)) : 0;
  return (
    <div className="cost-panel space-y-1">
      <h4 className="font-semibold">Cost</h4>
      <div className="cost-total text-lg">
        {totalUsd == null ? "—" : `$${totalUsd.toFixed(4)}`}
      </div>
      {entries.map(([step, usd]) => (
        <div key={step} className="cost-row">
          <div className="flex items-center justify-between text-sm">
            <span className="cost-step">{step}</span>
            <span className="cost-val text-gray-600">${usd.toFixed(4)}</span>
          </div>
          <div className="h-2 bg-gray-200 rounded mt-0.5">
            <span
              className="cost-bar block h-2 bg-blue-600 rounded"
              style={{ width: `${max ? (usd / max) * 100 : 0}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
