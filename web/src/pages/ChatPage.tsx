import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { listKbs, query as apiQuery } from "../api/client";
import { QUERY_METHODS } from "../lib/query-methods";
import { cn } from "../lib/cn";
import { Card, CardHeader, Button, Spinner, Badge, EmptyState } from "../components/ui";
import { QueryResultView } from "../components/QueryResultView";
import type { SourceRef } from "../api/types";
import { IconChat, IconSparkle, IconWarn, IconClock, IconDatabase } from "../components/icons";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  method?: string;
  elapsedMs?: number;
  error?: string | null;
  // Server-side fields (snake_case to match the API QueryResult wire format)
  prompt_tokens?: number;
  output_tokens?: number;
  llm_calls?: number;
  sources?: SourceRef[];
}

/** Chat-style Q&A: left KB picker, middle transcript, reuses the query() API. */
export default function ChatPage() {
  const kbs = useAsync(() => listKbs(), []);
  const list = kbs.data ?? [];

  const [kbId, setKbId] = useState<number | null>(null);
  const [method, setMethod] = useState("local");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const seq = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (kbId == null && list.length > 0) setKbId(list[0].id);
  }, [list, kbId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    if (kbId == null || !input.trim() || busy) return;
    const q = input.trim();
    const userMsg: Message = { id: ++seq.current, role: "user", text: q };
    const pendingId = ++seq.current;
    setMessages((m) => [...m, userMsg, { id: pendingId, role: "assistant", text: "" }]);
    setInput("");
    setBusy(true);
    const t0 = performance.now();
    try {
      const r = await apiQuery(kbId, method, q);
      const elapsedMs = performance.now() - t0;
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? {
                ...msg,
                text: r.answer,
                method: r.method,
                elapsedMs,
                error: r.error,
                prompt_tokens: r.prompt_tokens,
                output_tokens: r.output_tokens,
                llm_calls: r.llm_calls,
                sources: r.sources,
              }
            : msg,
        ),
      );
    } catch (e) {
      const elapsedMs = performance.now() - t0;
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, text: "", method, elapsedMs, error: (e as Error).message ?? String(e) }
            : msg,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  if (list.length === 0 && !kbs.loading) {
    return (
      <EmptyState
        icon={<IconChat />}
        title="还没有可对话的知识库"
        hint="先创建知识库并完成一次索引，再回来进行问答对话。"
        action={<Link to="/kbs" className="btn btn-primary btn-sm">前往知识库管理</Link>}
      />
    );
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
      {/* Left: KB + method */}
      <div className="space-y-4">
        <Card>
          <CardHeader title="知识库" icon={<IconDatabase width={18} height={18} />} />
          <div className="mt-3 space-y-1">
            {list.map((k) => (
              <button
                key={k.id}
                onClick={() => setKbId(k.id)}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors",
                  kbId === k.id ? "bg-brand-50 text-brand-700" : "text-body hover:bg-surface-2",
                )}
              >
                <span className="truncate font-medium">{k.name}</span>
                <Badge tone={kbId === k.id ? "brand" : "neutral"}>{k.method}</Badge>
              </button>
            ))}
            {kbs.loading && <p className="px-3 text-[12px] text-muted">加载中…</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="检索方式" icon={<IconSparkle width={18} height={18} />} />
          <div className="mt-3 space-y-1.5">
            {QUERY_METHODS.map((m) => (
              <button
                key={m.key}
                onClick={() => setMethod(m.key)}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors",
                  method === m.key
                    ? "border-brand bg-brand-50/60"
                    : "border-line bg-surface hover:bg-surface-2",
                )}
              >
                <span className="font-mono text-[13px] font-medium text-ink">{m.name}</span>
                {m.needsReports && (
                  <span className="rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] text-[#b26b00]">
                    需报告
                  </span>
                )}
              </button>
            ))}
          </div>
        </Card>
      </div>

      {/* Middle: transcript + input */}
      <Card pad={false} className="flex h-[calc(100vh-220px)] min-h-[420px] flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <IconChat width={18} height={18} className="text-brand" />
            <span className="text-[15px] font-semibold text-ink">问答对话</span>
          </div>
          {messages.length > 0 && (
            <button
              onClick={() => setMessages([])}
              className="text-[12px] text-muted hover:text-ink hover:underline"
            >
              清空对话
            </button>
          )}
        </div>

        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-grad-soft text-brand">
                <IconSparkle width={22} height={22} />
              </span>
              <p className="text-sm font-medium text-ink">开始提问</p>
              <p className="mt-1 max-w-xs text-[13px]">
                在下方输入问题，基于所选知识库的图谱用 <span className="font-mono">{method}</span> 方式检索回答。
              </p>
            </div>
          ) : (
            messages.map((m) => <ChatBubble key={m.id} m={m} />)
          )}
        </div>

        <div className="border-t border-line px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              className="textarea h-12 resize-none py-2.5"
              placeholder={`向知识库提问（${method} 方式）…  ⌘/Ctrl + Enter 发送`}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
              }}
            />
            <Button variant="primary" disabled={busy || !input.trim() || kbId == null} onClick={send}>
              {busy ? <Spinner /> : <IconSparkle width={16} height={16} />}
              {busy ? "回答中…" : "发送"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function ChatBubble({ m }: { m: Message }) {
  const isUser = m.role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-brand px-4 py-2.5 text-sm text-white">
          {m.text}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <div className="flex items-center gap-1.5 text-[11px] text-muted">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-grad-soft text-brand">
            <IconSparkle width={13} height={13} />
          </span>
          {m.method && <Badge tone="brand">{m.method}</Badge>}
          {m.elapsedMs != null && (
            <span className="flex items-center gap-0.5 nums">
              <IconClock width={12} height={12} /> {m.elapsedMs.toFixed(0)} ms
            </span>
          )}
          {m.text === "" && !m.error && <span className="flex items-center gap-1"><Spinner /> 生成中…</span>}
        </div>
        {m.error ? (
          <div className="flex items-start gap-2 rounded-2xl rounded-tl-sm bg-danger-soft px-4 py-2.5 text-[13px] text-danger">
            <IconWarn width={15} height={15} className="mt-0.5 shrink-0" />
            <span>{m.error}</span>
          </div>
        ) : (
          <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed text-ink">
            {m.text}
          </div>
        )}
        {m.error ? null : (
          <QueryResultView
            result={{
              answer: m.text,
              method: m.method ?? "local",
              error: m.error ?? null,
              elapsed_ms: m.elapsedMs,
              prompt_tokens: m.prompt_tokens,
              output_tokens: m.output_tokens,
              llm_calls: m.llm_calls,
              sources: m.sources,
            }}
          />
        )}
      </div>
    </div>
  );
}
