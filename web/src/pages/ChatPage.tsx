import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import {
  listKbs,
  listConversations,
  createConversation,
  getConversation,
  deleteConversation,
  sendMessage,
} from "../api/client";
import { QUERY_METHODS } from "../lib/query-methods";
import { cn } from "../lib/cn";
import { Card, CardHeader, Button, Spinner, Badge, EmptyState } from "../components/ui";
import { QueryResultView } from "../components/QueryResultView";
import type { SourceRef, Conversation, ChatMessage } from "../api/types";
import { IconChat, IconSparkle, IconWarn, IconClock, IconDatabase, IconPlus, IconTrash } from "../components/icons";

// Local ids for optimistic bubbles are negative so they never clash with server ids.
let seq = 0;

/** Multi-turn chat: KB picker | conversation sidebar | transcript. */
export default function ChatPage() {
  const kbs = useAsync(() => listKbs(), []);
  const list = kbs.data ?? [];

  const [kbId, setKbId] = useState<number | null>(null);
  const [convId, setConvId] = useState<number | null>(null);
  const [convList, setConvList] = useState<Conversation[]>([]);
  const [method, setMethod] = useState("local");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (kbId == null && list.length > 0) setKbId(list[0].id);
  }, [list, kbId]);

  // Load conversation list whenever the KB changes; reset the open conversation.
  useEffect(() => {
    if (kbId == null) {
      setConvList([]);
      setConvId(null);
      setMessages([]);
      return;
    }
    let alive = true;
    listConversations(kbId)
      .then((cs) => {
        if (alive) {
          setConvList(cs);
          setConvId(null);
          setMessages([]);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [kbId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const reloadList = async () => {
    if (kbId == null) return;
    try {
      setConvList(await listConversations(kbId));
    } catch {
      /* ignore */
    }
  };

  const selectConv = async (id: number) => {
    setConvId(id);
    try {
      setMessages((await getConversation(id)).messages);
    } catch {
      setMessages([]);
    }
  };

  const newConversation = async () => {
    if (kbId == null) return;
    try {
      const c = await createConversation(kbId);
      setConvList((cs) => [c, ...cs]);
      setConvId(c.id);
      setMessages([]);
    } catch {
      /* ignore */
    }
  };

  const removeConv = async (id: number) => {
    try {
      await deleteConversation(id);
    } catch {
      /* ignore */
    }
    setConvList((cs) => cs.filter((c) => c.id !== id));
    if (convId === id) {
      setConvId(null);
      setMessages([]);
    }
  };

  const send = async () => {
    if (kbId == null || convId == null || !input.trim() || busy) return;
    const q = input.trim();
    const userId = --seq;
    const pendingId = --seq;
    setMessages((m) => [
      ...m,
      { id: userId, role: "user", content: q },
      { id: pendingId, role: "assistant", content: "", method, rewrite_fell_back: false },
    ]);
    setInput("");
    setBusy(true);
    const t0 = performance.now();
    try {
      const r = await sendMessage(convId, q, method);
      const fallbackElapsed = performance.now() - t0;
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId ? { ...r, elapsed_ms: r.elapsed_ms ?? fallbackElapsed } : msg,
        ),
      );
      void reloadList(); // refresh sidebar snippet/title
    } catch (e) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, content: "", error: (e as Error).message ?? String(e) }
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
    <div className="grid gap-5 lg:grid-cols-[220px_240px_1fr]">
      {/* Col 1: KB picker */}
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

      {/* Col 2: conversations */}
      <Card pad={false} className="flex max-h-[calc(100vh-180px)] flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <span className="text-[13px] font-semibold text-ink">对话</span>
          <Button variant="ghost" size="sm" disabled={kbId == null} onClick={newConversation}>
            <IconPlus width={14} height={14} /> 新建
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto p-2">
          {convList.length === 0 ? (
            <p className="px-2 py-4 text-center text-[12px] text-muted">点击「新建」开始一段对话</p>
          ) : (
            convList.map((c) => (
              <div
                key={c.id}
                className={cn(
                  "group flex items-center gap-1 rounded-lg px-2 py-2 text-left transition-colors",
                  convId === c.id ? "bg-brand-50" : "hover:bg-surface-2",
                )}
              >
                <button onClick={() => selectConv(c.id)} className="min-w-0 flex-1 text-left">
                  <div className="truncate text-[13px] font-medium text-ink">{c.title || "新对话"}</div>
                  <div className="truncate text-[11px] text-muted">{c.snippet || "（暂无消息）"}</div>
                </button>
                <button
                  onClick={() => removeConv(c.id)}
                  className="shrink-0 text-muted opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
                  title="删除对话"
                >
                  <IconTrash width={13} height={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* Col 3: transcript */}
      <Card pad={false} className="flex h-[calc(100vh-180px)] min-h-[420px] flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <IconChat width={18} height={18} className="text-brand" />
            <span className="text-[15px] font-semibold text-ink">问答对话</span>
          </div>
          <div className="flex items-center gap-1">
            {QUERY_METHODS.map((m) => (
              <button
                key={m.key}
                onClick={() => setMethod(m.key)}
                className={cn(
                  "rounded-md border px-2 py-1 text-[12px] font-mono",
                  method === m.key ? "border-brand bg-brand-50 text-brand-700" : "border-line text-body",
                )}
              >
                {m.name}
              </button>
            ))}
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {convId == null ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-grad-soft text-brand">
                <IconSparkle width={22} height={22} />
              </span>
              <p className="text-sm font-medium text-ink">开始提问</p>
              <p className="mt-1 max-w-xs text-[13px]">在左侧「新建」一段对话，后续提问会参考上下文。</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <p className="text-[13px]">
                用 <span className="font-mono">{method}</span> 方式提问。后续追问会自动结合上下文改写。
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
              placeholder={
                convId == null
                  ? "先「新建」一段对话…"
                  : `向知识库提问（${method} 方式）…  ⌘/Ctrl + Enter 发送`
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
              }}
              disabled={convId == null}
            />
            <Button variant="primary" disabled={busy || !input.trim() || convId == null} onClick={send}>
              {busy ? <Spinner /> : <IconSparkle width={16} height={16} />}
              {busy ? "回答中…" : "发送"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function ChatBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-brand px-4 py-2.5 text-sm text-white">
          {m.content}
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
          {m.elapsed_ms != null && (
            <span className="flex items-center gap-0.5 nums">
              <IconClock width={12} height={12} /> {m.elapsed_ms.toFixed(0)} ms
            </span>
          )}
          {m.rewrite_fell_back && <span className="text-warning">(改写失败，已按原文检索)</span>}
          {m.content === "" && !m.error && (
            <span className="flex items-center gap-1">
              <Spinner /> 生成中…
            </span>
          )}
        </div>
        {m.rewritten_query && (
          <div className="text-[11px] text-muted">
            理解为：<span className="font-mono text-ink/70">{m.rewritten_query}</span>
          </div>
        )}
        {m.error ? (
          <div className="flex items-start gap-2 rounded-2xl rounded-tl-sm bg-danger-soft px-4 py-2.5 text-[13px] text-danger">
            <IconWarn width={15} height={15} className="mt-0.5 shrink-0" />
            <span>{m.error}</span>
          </div>
        ) : (
          <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed text-ink">
            {m.content}
          </div>
        )}
        {!m.error && (
          <QueryResultView
            result={{
              answer: m.content,
              method: m.method ?? "local",
              error: m.error ?? null,
              elapsed_ms: m.elapsed_ms ?? undefined,
              prompt_tokens: m.prompt_tokens ?? undefined,
              output_tokens: m.output_tokens ?? undefined,
              sources: m.sources as SourceRef[] | undefined,
            }}
          />
        )}
      </div>
    </div>
  );
}
