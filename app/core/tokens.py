"""token 计数。

tiktoken 首次使用需要下载编码表，离线环境会失败。这里做了降级：
失败时按中文约 1.5 字/token 估算，保证分块与上下文预算永远可用。
"""

from __future__ import annotations

from functools import lru_cache

_CHARS_PER_TOKEN = 1.5


@lru_cache
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    enc = _encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, int(len(text) / _CHARS_PER_TOKEN))
