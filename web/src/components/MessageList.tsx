import { Markdown } from "./Markdown";

export type ChatMessage = {
  id: string;
  /** 服务端消息 id：点赞点踩要靠它，本地流式消息在 done 事件后才有 */
  remoteId?: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  interrupted?: boolean;
  noAnswer?: boolean;
  usedCitations?: number[];
};

type Props = {
  messages: ChatMessage[];
  onCitationClick: (index: number) => void;
  onRetry?: () => void;
  onFeedback?: (messageId: string, rating: "up" | "down") => void;
  onRegenerate?: () => void;
  feedback?: Record<string, "up" | "down">;
};

const ACTION_BASE = "rounded px-1.5 py-0.5 text-xs transition";

export function MessageList({
  messages,
  onCitationClick,
  onRetry,
  onFeedback,
  onRegenerate,
  feedback = {},
}: Props) {
  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-zinc-600">选择一个知识库开始提问</p>
        <div className="space-y-1 text-xs text-zinc-500">
          <p>试试：年假有多少天？</p>
          <p>试试：整机保修期多久？</p>
          <p>试试：SKU-8823 的尺寸是多少？</p>
        </div>
      </div>
    );
  }

  const lastAssistantId = [...messages].reverse().find((item) => item.role === "assistant")?.id;

  return (
    <div className="space-y-4 px-4 py-4">
      {messages.map((message) => {
        const isUser = message.role === "user";
        const key = message.remoteId ?? message.id;
        const rated = feedback[key];
        const isLast = message.id === lastAssistantId;

        return (
          <div key={message.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] ${isUser ? "order-1" : ""}`}>
              <div
                className={`whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-relaxed ${
                  isUser
                    ? "bg-blue-600 text-white"
                    : message.noAnswer
                      ? "border border-zinc-200 bg-zinc-50 text-zinc-700"
                      : "border border-zinc-200 bg-white text-zinc-900"
                }`}
              >
                {isUser ? (
                  <span className="whitespace-pre-wrap">{message.content}</span>
                ) : (
                  <Markdown content={message.content} onCitationClick={onCitationClick} />
                )}
                {message.streaming ? (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-zinc-400 align-middle" />
                ) : null}
              </div>

              {message.interrupted ? (
                <div className="mt-1 flex items-center gap-2 text-xs text-amber-700">
                  <span>生成中断</span>
                  {onRetry ? (
                    <button type="button" onClick={onRetry} className="underline">
                      重试
                    </button>
                  ) : null}
                </div>
              ) : null}

              {!isUser && !message.streaming && !message.noAnswer ? (
                <div className="mt-1 flex items-center gap-1 text-zinc-400">
                  {onFeedback && message.remoteId ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onFeedback(key, "up")}
                        className={`${ACTION_BASE} ${
                          rated === "up"
                            ? "bg-emerald-50 text-emerald-700"
                            : "hover:bg-zinc-100 hover:text-zinc-600"
                        }`}
                        aria-label="答案有用"
                        title="答案有用"
                      >
                        ▲ 有用
                      </button>
                      <button
                        type="button"
                        onClick={() => onFeedback(key, "down")}
                        className={`${ACTION_BASE} ${
                          rated === "down"
                            ? "bg-red-50 text-red-700"
                            : "hover:bg-zinc-100 hover:text-zinc-600"
                        }`}
                        aria-label="答案有误"
                        title="答案有误，会记录当时的检索内容供改进"
                      >
                        ▼ 有误
                      </button>
                    </>
                  ) : null}
                  {isLast && onRegenerate ? (
                    <button
                      type="button"
                      onClick={onRegenerate}
                      className={`${ACTION_BASE} hover:bg-zinc-100 hover:text-zinc-600`}
                    >
                      重新回答
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
