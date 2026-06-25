const COLORS: Record<string, string> = {
  succeeded: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  running: "bg-blue-100 text-blue-800",
  pending: "bg-gray-100 text-gray-700",
  partially_failed: "bg-yellow-100 text-yellow-800",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${COLORS[status] ?? "bg-gray-100"}`}>
      {status}
    </span>
  );
}
