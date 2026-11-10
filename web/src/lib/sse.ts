import { clearToken, getToken } from "./auth";

export type Citation = {
  index: number;
  document_id: string;
  chunk_id?: string | null;
  title: string;
  heading_path: string | null;
  page_from: number;
};

export type StreamHandlers = {
  onCitations?: (citations: Citation[]) => void;
  onToken?: (text: string) => void;
  onNoAnswer?: (message: string) => void;
  onDone?: (payload: { message_id: string; conversation_id?: string; used_citations: number[] }) => void;
  onError?: (message: string, status?: number) => void;
};

/** EventSource 无法携带 Authorization 头，因此用 fetch + ReadableStream 解析 SSE。 */
export async function streamChat(
  body: { kb_ids: string[]; query: string; conversation_id?: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const response = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (response.status === 401) {
    clearToken();
    window.location.href = "/login";
    return;
  }

  if (!response.ok) {
    const text = await response.text();
    let message = "请求失败，请稍后重试";
    try {
      message = JSON.parse(text)?.error?.message ?? message;
    } catch {
      /* 保持默认文案 */
    }
    handlers.onError?.(message, response.status);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    handlers.onError?.("浏览器不支持流式响应");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      const rawEvent = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      dispatch(rawEvent, handlers);
      separator = buffer.indexOf("\n\n");
    }
  }
}

function dispatch(rawEvent: string, handlers: StreamHandlers): void {
  let eventName = "";
  let dataLine = "";
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
  }
  if (!eventName || !dataLine) return;

  let payload: any;
  try {
    payload = JSON.parse(dataLine);
  } catch {
    return;
  }

  switch (eventName) {
    case "citations":
      handlers.onCitations?.(payload.citations ?? []);
      break;
    case "token":
      handlers.onToken?.(payload.t ?? "");
      break;
    case "no_answer":
      handlers.onNoAnswer?.(payload.message ?? "");
      break;
    case "done":
      handlers.onDone?.(payload);
      break;
    case "error":
      handlers.onError?.(payload.message ?? "生成失败");
      break;
  }
}
