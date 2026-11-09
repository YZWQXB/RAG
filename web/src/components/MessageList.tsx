import { Fragment } from "react";

export type ChatMessage = {
  id: string;
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
};

const CITATION_PATTERN = /\[(\d{1,2})\]/g;

function renderContent(content: string, onCitationClick: (index: number) => void) {
  if (!content) return null;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  CITATION_PATTERN.lastIndex = 0;
  while ((match = CITATION_PATTERN.exec(content)) !== null) {
    const index = Number(match[1]);
    if (match.index > cursor) {
      parts.push(<Fragment key={`t-${cursor}`}>{content.slice(cursor, match.index)}</Fragment>);
    }
    parts.push(
      <button
        type="button"
        key={`c-${match.index}`}
        onClick={() => onCitationClick(index)}
        className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-blue-100 px-1 align-super text-[11px] font-medium text-blue-700 hover:bg-blue-200"
        aria-label={`查看第 ${index} 条引用`}
      >
        {index}
      </button>,
    );
    cursor = match.index + match[0].length;
  }

  if (cursor < content.length) {
    parts.push(<Fragment key="tail">{content.slice(cursor)}</Fragment>);
  }
  return parts;
}

export function MessageList({ messages, onCitationClick, onRetry }: Props) {
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

  return (
    <div className="space-y-4 px-4 py-4">
      {messages.map((message) => {
        const isUser = message.role === "user";
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
                {renderContent(message.content, onCitationClick)}
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
            </div>
          </div>
        );
      })}
    </div>
  );
}
