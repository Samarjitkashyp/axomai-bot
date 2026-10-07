from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, HttpUrl, field_validator


class CrawlRequest(BaseModel):
    url: str = Field(..., description="Starting seed URL to crawl, e.g. https://example.com")
    max_pages: int = Field(default=25, ge=1, le=5000, description="Maximum pages to crawl")
    max_depth: int = Field(default=3, ge=1, le=20, description="Max BFS depth")
    crawl_delay: float = Field(default=1.0, ge=0.0, le=10.0, description="Delay between requests in seconds")
    render_js: bool = Field(default=False, description="Enable Playwright headless browser for JavaScript-heavy sites")
    force_recrawl: bool = Field(default=False, description="Force re-crawling even if content has not changed")
    include_subdomains: bool = Field(default=False, description="Also crawl sibling subdomains of the seed domain")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v


class CrawlStartResponse(BaseModel):
    job_id: str
    message: str
    status: str
    seed_url: str


class PageSummary(BaseModel):
    url: str
    title: str
    word_count: int
    change_status: Optional[str] = "new"


class JobStatusResponse(BaseModel):
    job_id: str
    seed_url: str
    status: str
    pages_crawled: int
    pages_new: int = 0
    pages_updated: int = 0
    pages_skipped: int = 0
    force_recrawl: bool = False
    max_pages: int
    max_depth: int
    render_js: bool
    current_url: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0
    saved_file_path: Optional[str] = None
    error_count: int = 0
    latest_logs: List[str] = []
    latest_pages: List[PageSummary] = []


class SavedJobItem(BaseModel):
    job_id: Optional[str] = None
    seed_url: Optional[str] = None
    status: Optional[str] = None
    total_pages: int = 0
    pages_new: int = 0
    pages_updated: int = 0
    pages_skipped: int = 0
    file_size_bytes: int = 0
    completed_at: Optional[str] = None
    filename: str


# --- RAG Models ---

class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=2, description="Question or search phrase")
    top_k: int = Field(default=4, ge=1, le=20, description="Number of matching passages to return")


class RAGMatch(BaseModel):
    chunk_id: str
    text: str
    url: str
    title: str
    job_id: str
    similarity: float
    distance: float


class RAGQueryResponse(BaseModel):
    query: str
    total_matches: int
    matches: List[RAGMatch]


class RAGIndexResponse(BaseModel):
    job_id: str
    indexed_chunks: int
    total_vectors_in_db: int
    message: str


class SourceItem(BaseModel):
    url: str
    title: str
    similarity: float


class RAGChatRequest(BaseModel):
    question: str = Field(..., min_length=2, description="User question to answer using RAG")
    top_k: int = Field(default=4, ge=1, le=10, description="Number of context passages to retrieve")
    provider: str = Field(default="auto", description="LLM provider: auto, gemini, ollama, fallback")
    api_key: Optional[str] = Field(default=None, description="Optional Google Gemini API key")


class RAGChatResponse(BaseModel):
    question: str
    answer: str
    sources: List[SourceItem]
    provider_used: str

