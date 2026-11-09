import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/Alert";
import { CitationPanel } from "../components/CitationPanel";
import { MessageList, type ChatMessage } from "../components/MessageList";
import { QuotaBanner } from "../components/QuotaBanner";
import { ApiError, api } from "../lib/api";
import { streamChat, type Citation } from "../lib/sse";
import type { Kb } from "./KbList";

type Conversation = { id: string; title: string; kb_ids: string[]; message_count: number };

type RemoteMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string;
  citations: { document_id: string; title: string; heading_path: string | null; page_from: number }[];
};

export function Chat() {
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [quota, setQuota] = useState<{ message: string; limit?: number; used?: number } | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [kbList, conversationList] = await Promise.all([
          api.get<Kb[]>("/api/v1/kbs"),
          api.get<Conversation[]>("/api/v1/conversations"),
        ]);
        setKbs(kbList);
        setSelected(kbList.map((kb) => kb.id));
        setConversations(conversationList);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      }
    })();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const currentCitations = useMemo(() => citations, [citations]);

  const openConversation = async (id: string) => {
    setConversationId(id);
    setError("");
    try {
      const history = await api.get<RemoteMessage[]>(`/api/v1/conversations/${id}/messages`);
      setMessages(
        history.map((item) => ({
          id: item.id,
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

    try {
      await streamChat(
        {
          kb_ids: selected,
          query: trimmed,
          ...(conversationId ? { conversation_id: conversationId } : {}),
        },
        {
          onCitations: (list) => setCitations(list),
          onToken: (text) =>
            setMessages((prev) =>
              prev.map((item) =>
                item.id === assistantId ? { ...item, content: item.content + text } : item,
              ),
            ),
          onNoAnswer: (message) =>
            patchAssistant({ content: message, noAnswer: true, streaming: false }),
          onDone: (payload) => {
            patchAssistant({ streaming: false, interrupted: false });
            if (payload.conversation_id) setConversationId(payload.conversation_id);
            void api
              .get<Conversation[]>("/api/v1/conversations")
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
      );
    } catch {
      patchAssistant({ streaming: false, interrupted: true });
      setError("网络中断，请重试");
    } finally {
      setStreaming(false);
    }
  };

  const toggleKb = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-zinc-200 bg-white lg:flex">
        <div className="border-b border-zinc-100 p-3">
          <button
            type="button"
            onClick={() => {
              setConversationId(null);
              setMessages([]);
              setCitations([]);
            }}
            className="w-full rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          >
            新会话
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 ? (
            <p className="p-2 text-xs text-zinc-500">还没有会话</p>
          ) : (
            conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                onClick={() => void openConversation(conversation.id)}
                className={`mb-1 block w-full truncate rounded px-2 py-1.5 text-left text-sm ${
                  conversationId === conversation.id
                    ? "bg-blue-50 text-blue-900"
                    : "text-zinc-700 hover:bg-zinc-100"
                }`}
              >
                {conversation.title}
              </button>
            ))
          )}
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        {quota ? (
          <QuotaBanner message={quota.message} limit={quota.limit} used={quota.used} />
        ) : null}

        <div className="border-b border-zinc-200 bg-white px-4 py-2">
          <p className="mb-1 text-xs text-zinc-500">检索范围</p>
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
            onCitationClick={(index) => setActiveCitation(index)}
            onRetry={() => void send(lastQuery)}
          />
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
            <button
              type="submit"
              disabled={streaming || !input.trim()}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {streaming ? "生成中…" : "发送"}
            </button>
          </form>
        </div>
      </main>

      <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-zinc-200 bg-white lg:block">
        <CitationPanel
          citations={currentCitations}
          activeIndex={activeCitation}
          onSelect={setActiveCitation}
        />
      </aside>
    </div>
  );
}
