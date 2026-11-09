"""本地 OpenAI 兼容模拟在线 Provider（在线模式接线验证用）。

对齐 app/providers 中三个 api 实现的真实协议：
- POST /v1/embeddings          （OpenAI 格式，ApiBgeEmbedding 调用）
- POST /v1/chat/completions    （OpenAI 流式格式，OpenAICompatLLM 调用）
- POST /v1/rerank              （SiliconFlow 格式，ApiBgeReranker 调用）

无外部依赖（标准库 http.server + asyncio SSE），输出确定性，仅用于验证
在线模式的请求构造、流式解析、错误处理与重排序接线是否正确。
启动：python scripts/mock_online_provider.py [port]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 1024

# 被调用的次数统计（用于验证 api Provider 确实被走到）
stats = {"embeddings": 0, "chat": 0, "rerank": 0}


# 问句停用词：模拟语义向量对「什么是X」「X是什么」等问句形式的归一化行为
STOPWORDS = (
    "什么是",
    "怎么",
    "如何",
    "为什么",
    "请问",
    "哪些",
    "哪个",
    "多少",
    "一下",
    "介绍",
    "解释",
    "意思",
    "什么",
    "么",
    "吗",
    "呢",
    "的",
    "了",
    "是",
    "啊",
    "吧",
)


def _normalize(text: str) -> str:
    for w in STOPWORDS:
        text = text.replace(w, "")
    return text


def _bucket(text: str) -> int:
    digest = hashlib.md5(text.encode()).digest()
    return int.from_bytes(digest[:4], "big") % DIM


def vector_for(text: str) -> list[float]:
    """确定性伪语义向量：问句归一化 + 字符袋 + 字符二元组。

    剔除问句停用词（模拟语义向量把「什么是递归」映射到「递归」附近的行为），
    一元组捕捉字符重合，二元组让「递归」「二分」等词级重合产生强信号。
    仍无真实语义能力，「汽车」与「轿车」不会接近。
    """
    chars = [c for c in _normalize(text) if not c.isspace()]
    vec = [0.0] * DIM
    for ch in chars:
        vec[_bucket(ch)] += 1.0
    for a, b in zip(chars, chars[1:], strict=False):
        vec[_bucket(a + b)] += 1.5
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and len(auth) > 10

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def do_GET(self):
        if self.path == "/stats":
            self._json(200, {"calls": stats})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/v1/embeddings":
            self.handle_embeddings()
        elif self.path == "/v1/chat/completions":
            self.handle_chat()
        elif self.path == "/v1/rerank":
            self.handle_rerank()
        else:
            self._json(404, {"error": "not found"})

    def handle_embeddings(self):
        stats["embeddings"] += 1
        if not self._auth_ok():
            self._json(401, {"error": "invalid key"})
            return
        body = self._read_body()
        texts = body.get("input") or []
        if isinstance(texts, str):
            texts = [texts]
        data = [
            {"object": "embedding", "index": i, "embedding": vector_for(t)}
            for i, t in enumerate(texts)
        ]
        self._json(200, {"object": "list", "data": data, "model": body.get("model", "?")})

    def handle_chat(self):
        stats["chat"] += 1
        if not self._auth_ok():
            self._json(401, {"error": "invalid key"})
            return
        body = self._read_body()
        messages = body.get("messages") or []
        user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        # 从 prompt 中提取用户问题与第一条参考资料摘录（对齐 generation/prompt.py 的格式）
        question = ""
        m = re.search(r"【问题】(.+)", user_text)
        if m:
            question = m.group(1).strip()
        excerpt = ""
        m = re.search(r"\[1\][^\n]*\n(.+)", user_text)
        if m:
            excerpt = m.group(1).strip()[:120]
        answer = f"【在线模式】关于「{question}」：{excerpt or '（未提供参考资料）'} [1]"
        # 落盘请求日志，用于核对应用侧 prompt 构造（仅接线验证期间）
        try:
            with open("mock_chat_requests.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(messages, ensure_ascii=False) + "\n")
        except OSError:
            pass
        chunks = [answer[i : i + 8] for i in range(0, len(answer), 8)]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for piece in chunks:
            event = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": piece},
                        "finish_reason": None,
                    }
                ]
            }
            data = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        done = b"data: [DONE]\n\n"
        finish = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        payload = f"data: {json.dumps(finish, ensure_ascii=False)}\n\n".encode() + done
        self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def handle_rerank(self):
        stats["rerank"] += 1
        if not self._auth_ok():
            self._json(401, {"error": "invalid key"})
            return
        body = self._read_body()
        query = body.get("query", "")
        docs = body.get("documents") or []
        qvec = vector_for(query)
        scored = [
            {"index": i, "relevance_score": round(abs(cosine(qvec, vector_for(d))), 4)}
            for i, d in enumerate(docs)
        ]
        scored.sort(key=lambda x: -x["relevance_score"])
        self._json(200, {"model": body.get("model", "?"), "results": scored})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock online provider listening on http://127.0.0.1:{port}/v1")
    server.serve_forever()


if __name__ == "__main__":
    main()
