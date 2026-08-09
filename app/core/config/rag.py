"""RAG / vector-store / legal-graph settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class RAGSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    VECTOR_STORE_PROVIDER: str = "chroma"
    VECTOR_STORE_COLLECTION_NAME: str = "documents"
    # Keep legal articles in an independent collection so document RAG and
    # legal retrieval never share tenant metadata or lifecycle operations.
    LEGAL_VECTOR_STORE_COLLECTION_NAME: str = "legal_articles"
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"
    QDRANT_PERSIST_DIR: str = "./qdrant_db"
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
    NEO4J_ENABLED: bool = False
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = Field(default=20, ge=1, le=100)
    LEGAL_GRAPH_EVIDENCE_BOOST: float = Field(default=0.001, ge=0.0, le=0.01)
    LEGAL_GRAPH_EVIDENCE_MAX_SUPPORT_COUNT: int = Field(default=3, ge=1, le=10)

    RAG_TOP_K: int = 5
    RAG_CONFIDENCE_THRESHOLD: float = 0.35
    RAG_MIN_RECALL_CANDIDATES: int = 8
    RAG_RECALL_MULTIPLIER: int = 3
    RAG_QUERY_VARIANT_LIMIT: int = 4
    RAG_CONTEXT_NEIGHBOR_WINDOW: int = 1
    RAG_CONTEXT_MAX_CHUNKS: int = 8
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 100
    RAG_EMBED_CACHE_ENABLED: bool = True
    RAG_EMBED_CACHE_CAPACITY: int = 256
    RAG_EMBED_CACHE_REDIS_ENABLED: bool = False
    RAG_EMBED_CACHE_TTL_SECONDS: int = 86400
    RAG_EMBED_CACHE_REDIS_PREFIX: str = "aibg:rag:embed"
    RAG_BM25_ENABLED: bool = True
    RAG_BM25_TOP_N: int = 100
    RAG_BM25_TTL_SECONDS: int = 300
    RAG_LLM_RERANK_ENABLED: bool = False
    RAG_LLM_RERANK_TOP_N: int = 5
    RAG_LLM_RERANK_MAX_CHARS: int = 400
    RAG_RERANK_ENGINE: str = "heuristic"  # heuristic（默认，零依赖）| bge（BGE-Reranker 交叉编码器，需 torch+模型）| llm（qwen-plus）
    RAG_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RAG_RERANK_TOP_N: int = 5
    RAG_RERANK_MAX_CHARS: int = 400
    RAG_RERANK_DEVICE: str = "cpu"
    RAG_QUERY_EXPANSION_ENABLED: bool = True
    RAG_QUERY_EXPANSION_MAX: int = 4
    RAG_QUERY_REWRITE_LLM_ENABLED: bool = False
    RAG_QUERY_REWRITE_LLM_MIN_CHARS: int = 24
    RAG_CONTEXT_MAX_TOKENS: int = 6000
    LEGAL_DENSE_RECALL_MULTIPLIER: int = 3
    LEGAL_DENSE_MIN_CANDIDATES: int = 30
    AGENTIC_RAG_ENABLED: bool = True
    AGENTIC_RAG_PLANNER_ENABLED: bool = True
    AGENTIC_RAG_MAX_RETRIEVAL_ROUNDS: int = Field(default=2, ge=1, le=3)
