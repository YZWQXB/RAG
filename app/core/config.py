"""应用配置。所有可调参数集中在这里，业务代码不读 os.environ。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-secret-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    env: Literal["local", "test", "production"] = "local"
    secret_key: str = DEFAULT_SECRET

    database_url: str = "postgresql+asyncpg://rag_app:rag_app_pw@localhost:5432/rag"
    migration_database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/rag"
    test_database_url: str = "postgresql+asyncpg://rag_app:rag_app_pw@localhost:5432/rag_test"

    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "rag-docs"
    s3_region: str = "us-east-1"

    # local：文件系统存储（默认，零依赖）；s3：MinIO / OSS / S3
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "data/storage"

    embedding_provider: Literal["api", "fake", "local"] = "api"
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    # local：进程内 CPU/GPU 推理，不依赖任何外部向量服务。
    # 模型走 HF，国内直连不通，默认使用 hf-mirror 镜像。
    embedding_local_device: str = "cpu"
    embedding_local_batch_size: int = 8
    hf_endpoint: str = "https://hf-mirror.com"

    llm_provider: Literal["api", "fake"] = "api"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    reranker_provider: Literal["api", "fake"] = "api"
    reranker_base_url: str = "https://api.siliconflow.cn/v1"
    reranker_api_key: str = ""
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    retrieval_top_dense: int = 40
    retrieval_top_sparse: int = 20
    retrieval_top_fused: int = 20
    retrieval_top_final: int = 6
    retrieval_budget_tokens: int = 3000
    # 无答案阈值与向量模型强相关：换 embedding 模型必须重跑
    # scripts.calibrate_threshold。bge-m3 实测可答 p50=0.742 / 不可答 max=0.513，
    # 取 0.55 分离度最好。
    retrieval_no_answer_threshold: float = 0.55
    # 离线走词法打分（余弦×覆盖率），量纲与向量余弦不同：
    # 实测应作答 0.12~0.43、应拒答最高 0.026，取 0.10。
    # 沿用 0.55 会让离线模式 100% 拒答。
    retrieval_no_answer_threshold_fake: float = 0.10
    # 稀疏通道（pg_trgm）单独的门槛：精确编号类查询稠密分低但字面命中高，
    # 只看稠密会误拒。
    retrieval_sparse_answer_threshold: float = 0.35
    # 离线模式逐块算字符袋相似度，需要把块读进内存；给一个上限防止大库把请求拖死
    offline_scan_limit: int = 2000
    retrieval_rerank_enabled: bool = True
    # 多轮指代消解：追问「它有什么限制」时改写检索查询。
    # 代价是每次改写多一次 LLM 调用，因此有门控、超时和缓存兜着。
    enable_llm_rewrite: bool = True
    # 超过这个长度视为自带上下文，不改写（15 字约等于一个短问句）
    query_rewrite_max_chars: int = 15
    # 改写只在「按原句检索不到依据」时触发，此时用户本来就要收到拒答，
    # 多等几秒换一个能答的结果是划算的。实测上游首字延迟约 6 秒，3 秒必然超时。
    query_rewrite_timeout_seconds: float = 10.0
    # 改写专用模型（留空则用主模型）。小模型首字更快，适配追问场景
    query_rewrite_model: str = ""

    upload_max_bytes: int = 50 * 1024 * 1024
    # zip 类容器（docx）解压后允许的体积上限，防解压炸弹
    upload_max_uncompressed_bytes: int = 200 * 1024 * 1024
    chunk_size_chars: int = 600
    chunk_overlap_chars: int = 90
    chunk_max_chars: int = 1200
    ingest_max_attempts: int = 3
    # 单文档处理总超时：worker 只有一个轮询循环，不能被子任务无限占用
    ingest_timeout_seconds: float = 300.0
    # 同时处理的入库文档数。bge-m3 在进程内抢占 torch 线程，开太大反而整体更慢
    ingest_max_concurrent: int = 2

    sse_keepalive_seconds: float = 15.0
    login_rate_limit_per_minute: int = 20
    # 注册也要限流：否则可被脚本批量刷号占库
    register_rate_limit_per_minute: int = 5
    # 问答接口按 user_id 限流：登录限流按 IP 是因为那时不知道用户是谁；
    # 问答已知身份，按人计数才能避免同出口 IP 的同事互相挤兑。
    chat_rate_limit_per_minute: int = 5
    # 用量达到配额的比例阈值，超过后前端标红并记一次告警日志
    quota_warn_ratio: float = 0.8
    quota_timezone: str = "Asia/Shanghai"

    enable_debug_api: bool = True
    # /metrics 只暴露聚合量，但仍建议在网关层限制访问来源
    metrics_enabled: bool = True

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.env != "local":
            if self.secret_key == DEFAULT_SECRET:
                raise ValueError("生产环境必须设置 SECRET_KEY")
            if self.embedding_provider == "fake":
                raise ValueError("生产环境禁止使用 fake embedding provider")
        if self.is_production and self.storage_backend == "local":
            # 本地存储挂在本机磁盘上：多实例或多 worker 时，
            # 写文件的进程和读文件的进程可能不是同一台机器，任务会静默失败。
            raise ValueError(
                "生产环境禁止使用本地文件存储：请改用 STORAGE_BACKEND=s3，"
                "否则多副本部署会丢失入库任务"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
