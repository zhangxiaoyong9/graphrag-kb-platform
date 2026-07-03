import type { QueryResult, SourceRef } from "../api/types";
import { Badge } from "./ui";
import { IconClock, IconWarn } from "./icons";
import { TruncatedNotice } from "./TruncatedNotice";

/** Render query metadata (method/elapsed/tokens), real sources, and errors.
 * Does NOT render the answer body — callers present that in their own layout. */
export function QueryResultView({
  result,
  clientElapsedMs,
}: {
  result: QueryResult;
  clientElapsedMs?: number;
}) {
  const elapsed = result.elapsed_ms ?? clientElapsedMs;
  const entities = result.sources?.filter((s) => s.kind === "entity") ?? [];
  const texts = result.sources?.filter((s) => s.kind !== "entity") ?? [];
  const hasTokens = result.prompt_tokens || result.output_tokens || result.llm_calls;

  return (
    <div className="space-y-3">
      {result.error && (
        <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          <IconWarn width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{result.error}</span>
        </div>
      )}

      {result.truncated && <TruncatedNotice />}

      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <Badge tone="brand">{result.method}</Badge>
        {elapsed != null && (
          <span className="flex items-center gap-1 nums">
            <IconClock width={13} height={13} /> {Math.round(elapsed)} ms
          </span>
        )}
        {hasTokens ? (
          <span className="nums">
            {result.prompt_tokens ?? 0} prompt · {result.output_tokens ?? 0} output
            {result.llm_calls ? ` · ${result.llm_calls} 次调用` : ""}
          </span>
        ) : null}
      </div>

      {result.sources && result.sources.length > 0 && (
        <div>
          <p className="mb-1.5 text-[12px] font-medium text-body">引用与来源</p>
          {entities.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {entities.map((e, i) => (
                <SourceChip key={`e-${i}-${e.name}`} s={e} />
              ))}
            </div>
          )}
          {texts.length > 0 && (
            <ul className="space-y-1.5">
              {texts.map((t, i) => (
                <li
                  key={`t-${i}`}
                  className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[12px] leading-relaxed text-body"
                >
                  {t.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {result.cypher && (
        <details className="rounded-lg border border-line bg-surface-2/60 px-3 py-2">
          <summary className="cursor-pointer text-[12px] font-medium text-body">生成的 Cypher</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-ink/80">
            {result.cypher}
          </pre>
        </details>
      )}
    </div>
  );
}

function SourceChip({ s }: { s: SourceRef }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-1 text-[12px]">
      <span className="font-medium text-ink">{s.name}</span>
      {s.text && <span className="text-muted">· {s.text.slice(0, 40)}</span>}
    </span>
  );
}
