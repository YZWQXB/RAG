import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/Alert";
import { CitationPanel } from "../components/CitationPanel";
import { Markdown } from "../components/Markdown";
import { MessageList, type ChatMessage } from "../components/MessageList";
import { QuotaBanner } from "../components/QuotaBanner";
import { ApiError, api, downloadFile } from "../lib/api";
import { streamChat, type Citation } from "../lib/sse";
import type { Kb } from "./KbList";

type Conversation = {
  id: string;
  title: string;
  kb_ids: string[];
  message_count: number;
  updated_at: string | null;
};

type RemoteMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string;
  citations: {
    document_id: string;
    chunk_id: string | null;
    title: string;
    heading_path: string | null;
    page_from: number;
  }[];
};

/** 分块正文常以「[文档: x | 章节: y]」开头，弹窗已单独显示这些信息，正文里去掉。 */
function stripChunkHeader(content: string): string {
  const lines = content.split("\n");
  if (lines[0]?.startsWith("[文档:")) {
    const remainder = lines[0].split("]").slice(1).join("]").trim();
    if (remainder) lines[0] = remainder;
    else lines.shift();
  }
  // 只剩 `*` / `-` / `**` 的行是空的列表标记，会把整段的两侧强调配对打乱
  const meaningful = lines.filter((line) => !/^\s*[*\-_]{1,3}\s*$/.test(line));
  return meaningful.join("\n").trim();
}

type ChunkSource = {
  id: string;
  title: string;
  heading_path: string | null;
  page_from: number;
  page_to: number;
  content: string;
};

export function Chat() {
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  // 后端做了指代消解时带上改写后的查询，让用户知道检索用的是什么
  const [rewrittenQuery, setRewrittenQuery] = useState<string | null>(null);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [quota, setQuota] = useState<{ message: string; limit?: number; used?: number } | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [feedback, setFeedback] = useState<Record<string, "up" | "down">>({});
  const [source, setSource] = useState<ChunkSource | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const kbList = await api.get<Kb[]>("/api/v1/kbs");
        setKbs(kbList);
        setSelected(kbList.map((kb) => kb.id));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      }
    })();
  }, []);

  // 搜索与排序都由服务端执行：会话会越积越多，不能全量拉回前端再筛
  useEffect(() => {
    const timer = setTimeout(() => {
      const params = new URLSearchParams();
      const keyword = search.trim();
      if (keyword) params.set("q", keyword);
      params.set("order", order);
      params.set("limit", "200");
      void api
        .get<Conversation[]>(`/api/v1/conversations?${params.toString()}`)
        .then(setConversations)
        .catch((err) => setError(err instanceof ApiError ? err.message : "加载会话失败"));
    }, 250);
    return () => clearTimeout(timer);
  }, [search, order]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const currentCitations = useMemo(() => citations, [citations]);

  const openConversation = async (id: string) => {
    setConversationId(id);
    setError("");
    // 历史消息不带改写信息，切换会话时必须清掉上一条回答留下的提示
    setRewrittenQuery(null);
    try {
      const history = await api.get<RemoteMessage[]>(`/api/v1/conversations/${id}/messages`);
      setMessages(
        history.map((item) => ({
          id: item.id,
          remoteId: item.id,
          role: item.role,
          content: item.content,
          noAnswer: item.status === "no_answer",
          interrupted: item.status === "incomplete",
        })),
      );
      const latestWithCitations = [...history].reverse().find((item) => item.citations.length > 0);
      setCitations(
        (latestWithCitations?.citations ?? []).map((citation, index) => ({
          index: index + 1,
          document_id: citation.document_id,
          chunk_id: citation.chunk_id,
          title: citation.title,
          heading_path: citation.heading_path,
          page_from: citation.page_from,
        })),
      );
      const conversation = conversations.find((item) => item.id === id);
      if (conversation) setSelected(conversation.kb_ids);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载会话失败");
    }
  };

  const send = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed || streaming) return;
    if (selected.length === 0) {
      setError("请先选择至少一个知识库");
      return;
    }

    setError("");
    setQuota(null);
    setLastQuery(trimmed);
    setInput("");
    setActiveCitation(null);

    const userMessage: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      content: trimmed,
    };
    const assistantId = `a-${Date.now()}`;
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      streaming: true,
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setCitations([]);
    setStreaming(true);

    const patchAssistant = (patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((item) => (item.id === assistantId ? { ...item, ...patch } : item)),
      );
    };

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        {
          kb_ids: selected,
          query: trimmed,
          ...(conversationId ? { conversation_id: conversationId } : {}),
        },
        {
          onCitations: (list, meta) => {
            setCitations(list);
            setRewrittenQuery(meta.rewrittenQuery);
          },
          onToken: (text) =>
            setMessages((prev) =>
              prev.map((item) =>
                item.id === assistantId ? { ...item, content: item.content + text } : item,
              ),
            ),
          onNoAnswer: (message) =>
            patchAssistant({ content: message, noAnswer: true, streaming: false }),
          onDone: (payload) => {
            patchAssistant({ streaming: false, interrupted: false, remoteId: payload.message_id });
            if (payload.conversation_id) setConversationId(payload.conversation_id);
            const params = new URLSearchParams();
            if (search.trim()) params.set("q", search.trim());
            params.set("order", order);
            void api
              .get<Conversation[]>(`/api/v1/conversations?${params.toString()}`)
              .then(setConversations)
              .catch(() => undefined);
          },
          onError: (message, status) => {
            patchAssistant({ streaming: false, interrupted: true });
            if (status === 429) {
              void api
                .get<{ usage: Record<string, { used: number; limit: number }> }>("/api/v1/quota")
                .then((report) => {
                  const item = report.usage.queries_per_month;
                  setQuota({ message, limit: item?.limit, used: item?.used });
                })
                .catch(() => setQuota({ message }));
            } else {
              setError(message);
            }
          },
        },
        controller.signal,
      );
    } catch (err) {
      // 主动停止：保留已生成的部分内容，标记为中断而不是报错
      if (err instanceof DOMException && err.name === "AbortError") {
        patchAssistant({ streaming: false, interrupted: true });
      } else {
        patchAssistant({ streaming: false, interrupted: true });
        setError("网络中断，请重试");
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
  };

  const openCitation = async (index: number) => {
    setActiveCitation(index);
    const citation = citations.find((item) => item.index === index);
    if (!citation?.chunk_id) return;

    setSourceLoading(true);
    try {
      const detail = await api.get<ChunkSource>(`/api/v1/chunks/${citation.chunk_id}`);
      setSource(detail);
    } catch {
      setError("引用原文加载失败，文档可能已被删除");
    } finally {
      setSourceLoading(false);
    }
  };

  const sendFeedback = async (messageId: string, rating: "up" | "down") => {
    setFeedback((prev) => ({ ...prev, [messageId]: rating }));
    try {
      await api.post(`/api/v1/conversations/messages/${messageId}/feedback`, { rating });
    } catch {
      setError("反馈提交失败，请稍后重试");
    }
  };

  const exportConversation = async () => {
    if (!conversationId) return;
    try {
      await downloadFile(`/api/v1/conversations/${conversationId}/export`, "会话.md");
    } catch {
      setError("导出失败，请稍后重试");
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.del(`/api/v1/conversations/${pendingDelete.id}`);
      setConversations((prev) => prev.filter((item) => item.id !== pendingDelete.id));
      if (conversationId === pendingDelete.id) {
        setConversationId(null);
        setMessages([]);
        setCitations([]);
      }
      setPendingDelete(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败，请稍后重试");
    } finally {
      setDeleting(false);
    }
  };

  const formatDate = (value: string | null) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  const toggleKb = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-zinc-200 bg-white lg:flex">
        <div className="space-y-2 border-b border-zinc-100 p-3">
          <button
            type="button"
            onClick={() => {
              setConversationId(null);
              setMessages([]);
              setCitations([]);
              setRewrittenQuery(null);
            }}
            className="w-full rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          >
            新会话
          </button>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索会话标题"
            className="w-full rounded border border-zinc-300 px-2 py-1 text-xs outline-none focus:border-blue-500"
            aria-label="搜索历史会话"
          />
          <div className="flex items-center justify-between text-[11px] text-zinc-500">
            <span>{conversations.length} 个会话</span>
            <button
              type="button"
              onClick={() => setOrder((prev) => (prev === "desc" ? "asc" : "desc"))}
              className="rounded px-1 py-0.5 hover:bg-zinc-100 hover:text-zinc-700"
              title={order === "desc" ? "当前：最新在前，点击切换为最早在前" : "当前：最早在前，点击切换为最新在前"}
            >
              {order === "desc" ? "最新优先 ↓" : "最早优先 ↑"}
            </button>
          </div>
        </div>
        {/* min-h-0 必须保留：flex 子项默认 min-height:auto，会让内部滚动区被撑开、滚轮失效 */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2 [scrollbar-width:thin]">
          {conversations.length === 0 ? (
            <p className="p-2 text-xs text-zinc-500">
              {search.trim() ? "没有匹配的会话" : "还没有会话"}
            </p>
          ) : (
            conversations.map((conversation) => {
              const active = conversationId === conversation.id;
              return (
                <div
                  key={conversation.id}
                  className={`group mb-1 flex items-start rounded ${
                    active ? "bg-blue-50" : "hover:bg-zinc-100"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => void openConversation(conversation.id)}
                    className={`min-w-0 flex-1 px-2 py-1.5 text-left ${
                      active ? "text-blue-900" : "text-zinc-700"
                    }`}
                  >
                    <span className="block truncate text-sm">{conversation.title}</span>
                    <span className="mt-0.5 block text-[11px] text-zinc-400">
                      {formatDate(conversation.updated_at)}
                      {conversation.message_count > 0 ? ` · ${conversation.message_count} 条` : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      setPendingDelete(conversation);
                    }}
                    className="mr-1 mt-1 shrink-0 rounded px-1 text-xs text-zinc-400 opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 focus:opacity-100"
                    aria-label={`删除会话 ${conversation.title}`}
                    title="删除会话"
                  >
                    ×
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        {quota ? (
          <QuotaBanner message={quota.message} limit={quota.limit} used={quota.used} />
        ) : null}

        <div className="border-b border-zinc-200 bg-white px-4 py-2">
          <div className="mb-1 flex items-center justify-between">
            <p className="text-xs text-zinc-500">检索范围</p>
            {conversationId ? (
              <button
                type="button"
                onClick={() => void exportConversation()}
                className="text-xs text-zinc-500 underline hover:text-zinc-700"
              >
                导出 Markdown
              </button>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            {kbs.length === 0 ? (
              <span className="text-xs text-zinc-500">
                还没有知识库，
                <Link to="/kbs" className="text-blue-700 underline">
                  去创建
                </Link>
              </span>
            ) : (
              kbs.map((kb) => (
                <label
                  key={kb.id}
                  className={`cursor-pointer rounded-full border px-3 py-1 text-xs ${
                    selected.includes(kb.id)
                      ? "border-blue-500 bg-blue-50 text-blue-800"
                      : "border-zinc-300 text-zinc-600"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={selected.includes(kb.id)}
                    onChange={() => toggleKb(kb.id)}
                  />
                  {kb.name}
                </label>
              ))
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {error ? (
            <div className="p-3">
              <Alert tone="error">{error}</Alert>
            </div>
          ) : null}
          <MessageList
            messages={messages}
            onCitationClick={(index) => void openCitation(index)}
            onRetry={() => void send(lastQuery)}
            onFeedback={(messageId, rating) => void sendFeedback(messageId, rating)}
            onRegenerate={() => void send(lastQuery)}
            feedback={feedback}
          />
          {rewrittenQuery ? (
            <div className="px-4 pb-3">
              <span className="inline-block rounded bg-zinc-100 px-2 py-1 text-[11px] text-zinc-500">
                已结合上文改写检索：{rewrittenQuery}
              </span>
            </div>
          ) : null}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-zinc-200 bg-white p-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void send(input);
            }}
            className="flex gap-2"
          >
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="输入问题，例如：年假有多少天？"
              className="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-blue-600"
              disabled={streaming}
            />
            {streaming ? (
              <button
                type="button"
                onClick={stop}
                className="rounded-md border border-zinc-300 px-4 py-2 text-sm text-zinc-700 hover:bg-zinc-50"
              >
                停止
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                发送
              </button>
            )}
          </form>
        </div>
      </main>

      <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-zinc-200 bg-white lg:block">
        <CitationPanel
          citations={currentCitations}
          activeIndex={activeCitation}
          onSelect={(index) => void openCitation(index)}
        />
      </aside>

      {pendingDelete ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 p-4"
          role="dialog"
          aria-modal="true"
          onClick={() => (deleting ? undefined : setPendingDelete(null))}
        >
          <div
            className="w-full max-w-sm rounded-lg bg-white p-5 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="text-sm font-medium text-zinc-900">删除这个会话？</h2>
            <p className="mt-2 text-xs leading-relaxed text-zinc-600">
              将删除「{pendingDelete.title}」及其全部消息，此操作不可恢复。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
                disabled={deleting}
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={deleting}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? "删除中…" : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {sourceLoading ? (
        <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center">
          <span className="rounded-full bg-zinc-900/80 px-3 py-1 text-xs text-white">
            正在加载引用原文…
          </span>
        </div>
      ) : null}

      {source ? (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-900/40 p-4"
          role="dialog"
          aria-modal="true"
          onClick={() => setSource(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-5 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-medium text-zinc-900">{source.title}</h2>
                <p className="mt-1 text-xs text-zinc-500">
                  {source.heading_path ?? "正文"} · 第 {source.page_from}
                  {source.page_to !== source.page_from ? `-${source.page_to}` : ""} 页
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSource(null)}
                className="rounded px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-100"
              >
                关闭
              </button>
            </div>
            <div className="max-h-[55vh] overflow-y-auto rounded-md bg-zinc-50 p-3">
              <Markdown
                content={stripChunkHeader(source.content)}
                onCitationClick={() => undefined}
              />
            </div>
            <p className="mt-3 text-xs text-zinc-400">
              这是回答引用到的原文分块，用于核实答案是否有据可依。
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
