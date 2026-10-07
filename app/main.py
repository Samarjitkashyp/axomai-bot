import os
import io
import csv
import uuid
import asyncio
import logging
import httpx
from typing import Dict, List, Any
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import (
    authenticate_user, create_session_cookie, get_current_user,
    LOGIN_PAGE_HTML, COOKIE_NAME, PUBLIC_PATHS,
)
from app.scheduler import (
    register_crawl, set_schedule_enabled, remove_schedule,
    get_all_schedules, scheduler_loop,
)

from app.models import (
    CrawlRequest,
    CrawlStartResponse,
    JobStatusResponse,
    SavedJobItem,
    RAGQueryRequest,
    RAGQueryResponse,
    RAGIndexResponse,
    RAGChatRequest,
    RAGChatResponse
)
from crawler.engine import CrawlerJob, CrawlerEngine
from crawler.storage import StorageManager
from rag.vector_store import VectorStoreManager
from rag.llm_client import LLMManager

app = FastAPI(
    title="Axom AI Web Crawler & RAG API",
    description="Multilingual web crawler and RAG pipeline with UTF-8 preservation, ChromaDB vector search, and background execution.",
    version="1.2.0"
)

# CORS enabled
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path.startswith("/docs") or path.startswith("/openapi") or path in PUBLIC_PATHS:
            return await call_next(request)
        user = get_current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        request.state.user = user
        return await call_next(request)


app.add_middleware(AuthMiddleware)

_scheduler_task = None


@app.on_event("startup")
async def start_scheduler():
    global _scheduler_task
    _scheduler_task = asyncio.create_task(
        scheduler_loop(storage_manager, active_jobs)
    )


@app.on_event("shutdown")
async def stop_scheduler():
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_PAGE_HTML.replace("{error}", ""))


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    user = authenticate_user(username, password)
    if not user:
        html = LOGIN_PAGE_HTML.replace(
            "{error}",
            '<div class="error">Invalid credentials or no bot access permission.</div>'
        )
        return HTMLResponse(html, status_code=401)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        create_session_cookie(user),
        max_age=86400 * 7,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# In-memory store for currently running or recent jobs
active_jobs: Dict[str, CrawlerJob] = {}
storage_manager = StorageManager()
vector_store = VectorStoreManager()
llm_manager = LLMManager()

# Static directory setup
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """
    Serves the modern single-page crawler dashboard.
    """
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Axom AI Crawler & RAG API is running! Visit /docs for Swagger UI.</h1>")


_import_logger = logging.getLogger("axomai.import")

CHAT_IMPORT_URL = os.getenv("CHAT_IMPORT_URL", "http://127.0.0.1:8000/api/import-crawl/")
BOT_IMPORT_TOKEN = os.getenv("BOT_IMPORT_TOKEN", "")


async def _push_to_chat(job_id: str, seed_url: str):
    if not BOT_IMPORT_TOKEN:
        _import_logger.warning("BOT_IMPORT_TOKEN not set — skipping chat import")
        return
    data = storage_manager.get_crawl_job(job_id)
    if not data:
        return
    pages = data.get("pages", [])
    if not pages:
        return
    # AXOMAI-BATCH: batches of 200 pages with retries (a single huge request could time out and lose the whole crawl)
    from app.chat_import import push_pages
    await push_pages(CHAT_IMPORT_URL, BOT_IMPORT_TOKEN, seed_url, job_id, pages)


# --- Crawler Endpoints ---

@app.post("/api/crawl/start", response_model=CrawlStartResponse)
async def start_crawl(request: CrawlRequest, background_tasks: BackgroundTasks):
    """
    Starts an asynchronous background crawling task for the given seed URL.
    """
    job_id = f"job_{uuid.uuid4().hex[:8]}"

    job = CrawlerJob(
        job_id=job_id,
        seed_url=request.url,
        max_pages=request.max_pages,
        max_depth=request.max_depth,
        crawl_delay=request.crawl_delay,
        render_js=request.render_js,
        force_recrawl=request.force_recrawl,
        include_subdomains=request.include_subdomains
    )
    active_jobs[job_id] = job

    engine = CrawlerEngine(job=job, storage_manager=storage_manager)

    async def _crawl_and_register():
        await engine.run()
        if job.status == "completed":
            register_crawl(request.url)
            await _push_to_chat(job_id, request.url)

    background_tasks.add_task(_crawl_and_register)

    return CrawlStartResponse(
        job_id=job_id,
        message=f"Crawling initiated for {request.url}",
        status="queued",
        seed_url=request.url
    )


@app.get("/api/crawl/status/{job_id}", response_model=JobStatusResponse)
async def get_crawl_status(job_id: str):
    """
    Fetches real-time status, progress, logs, and preview for an active or completed crawl.
    """
    if job_id in active_jobs:
        return active_jobs[job_id].to_dict()

    saved_doc = storage_manager.get_crawl_job(job_id)
    if saved_doc:
        stats = saved_doc.get("stats", {})
        pages = saved_doc.get("pages", [])
        return {
            "job_id": saved_doc.get("job_id"),
            "seed_url": saved_doc.get("seed_url"),
            "status": saved_doc.get("status", "completed"),
            "pages_crawled": saved_doc.get("total_pages_crawled", len(pages)),
            "pages_new": stats.get("pages_new", len(pages)),
            "pages_updated": stats.get("pages_updated", 0),
            "pages_skipped": stats.get("pages_skipped", 0),
            "force_recrawl": stats.get("force_recrawl", False),
            "max_pages": stats.get("max_pages_limit", len(pages)),
            "max_depth": stats.get("max_depth_limit", 3),
            "render_js": stats.get("render_js", False),
            "current_url": None,
            "started_at": saved_doc.get("created_at"),
            "completed_at": saved_doc.get("completed_at"),
            "duration_seconds": saved_doc.get("duration_seconds", 0.0),
            "saved_file_path": storage_manager.get_file_path(job_id, saved_doc.get("seed_url", "")),
            "error_count": len(stats.get("errors", [])),
            "latest_logs": ["[Finished] Data loaded from disk"],
            "latest_pages": [
                {
                    "url": p["url"],
                    "title": p["title"],
                    "word_count": p.get("word_count", 0),
                    "change_status": p.get("change_status", "new")
                }
                for p in pages[-5:]
            ]
        }

    raise HTTPException(status_code=404, detail=f"Crawl job '{job_id}' not found.")


@app.get("/api/crawl/jobs")
async def list_jobs():
    """
    Returns list of all saved crawl datasets on disk as well as any in-memory active jobs.
    """
    running_list = [
        {
            "job_id": job.job_id,
            "seed_url": job.seed_url,
            "status": job.status,
            "total_pages": job.pages_crawled,
            "pages_new": job.pages_new,
            "pages_updated": job.pages_updated,
            "pages_skipped": job.pages_skipped,
            "file_size_bytes": 0,
            "completed_at": job.completed_at,
            "filename": f"{job.job_id}.json"
        }
        for job in active_jobs.values()
        if job.status in ("queued", "running")
    ]

    saved_list = storage_manager.list_saved_jobs()

    seen_ids = set()
    combined = []

    for item in running_list:
        if item["job_id"] not in seen_ids:
            seen_ids.add(item["job_id"])
            combined.append(item)

    for item in saved_list:
        if item["job_id"] not in seen_ids:
            seen_ids.add(item["job_id"])
            combined.append(item)

    return {"jobs": combined}


@app.get("/api/crawl/download/{job_id}")
async def download_crawl_json(job_id: str):
    """
    Downloads the saved JSON file for a given crawl job.
    """
    for filename in os.listdir(storage_manager.output_dir):
        if filename.startswith(f"{job_id}_") and filename.endswith(".json"):
            filepath = os.path.join(storage_manager.output_dir, filename)
            return FileResponse(
                filepath,
                media_type="application/json",
                filename=filename
            )

    raise HTTPException(status_code=404, detail="JSON dataset file not found.")


@app.get("/api/crawl/view/{job_id}")
async def view_crawl_json(job_id: str):
    """
    Returns sanitized JSON data for in-browser preview modal.
    Only clean URL, Title, Content, Word Count, and Status are included.
    """
    data = storage_manager.get_crawl_job(job_id)
    if not data and job_id in active_jobs:
        job = active_jobs[job_id]
        data = {
            "job_id": job.job_id,
            "seed_url": job.seed_url,
            "status": job.status,
            "pages_crawled": job.pages_crawled,
            "pages": job.pages
        }

    if not data:
        raise HTTPException(status_code=404, detail="Job data not found.")

    clean_pages = []
    for p in data.get("pages", []):
        clean_pages.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "content": p.get("content", ""),
            "word_count": p.get("word_count", 0),
            "change_status": p.get("change_status", "new")
        })

    data_copy = dict(data)
    data_copy["pages"] = clean_pages
    return data_copy


@app.post("/api/crawl/stop/{job_id}")
async def stop_crawl_job(job_id: str):
    """
    Gracefully stops a running crawl job. Visited pages are preserved and saved.
    """
    if job_id in active_jobs:
        job = active_jobs[job_id]
        if job.status in ("queued", "running"):
            job.cancel()
            return {"job_id": job_id, "message": "Crawl stopping gracefully.", "status": "stopping"}
        return {"job_id": job_id, "message": f"Job is already {job.status}.", "status": job.status}

    raise HTTPException(status_code=404, detail="Active crawl job not found.")


@app.get("/api/crawl/export/{job_id}/csv")
async def export_crawl_csv(job_id: str):
    """
    Generates and streams an Excel-compatible CSV file containing ONLY the URL and clean Content.
    Encoded with UTF-8 BOM (utf-8-sig) so Assamese, Hindi, and English text render natively in Excel.
    """
    data = storage_manager.get_crawl_job(job_id)
    if not data and job_id in active_jobs:
        job = active_jobs[job_id]
        data = {
            "job_id": job.job_id,
            "seed_url": job.seed_url,
            "pages": job.pages
        }
    if not data:
        raise HTTPException(status_code=404, detail="Crawl data not found.")

    pages = data.get("pages", [])
    output = io.StringIO()
    writer = csv.writer(output)
    # Strictly only 2 columns: URL and Content
    writer.writerow(["URL", "Content"])

    for p in pages:
        writer.writerow([
            p.get("url", ""),
            p.get("content", "")
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    safe_domain = urlparse(data.get("seed_url", "crawl")).netloc.replace("www.", "") or "export"
    filename = f"{job_id}_{safe_domain}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/crawl/export/{job_id}/pdf")
async def export_crawl_pdf(job_id: str):
    """
    Generates and streams a clean, printable PDF document containing ONLY Link and Clean Content.
    Uses Playwright Chromium for 100% native Unicode font rendering of Assamese, Hindi, and English.
    """
    data = storage_manager.get_crawl_job(job_id)
    if not data and job_id in active_jobs:
        job = active_jobs[job_id]
        data = {
            "job_id": job.job_id,
            "seed_url": job.seed_url,
            "pages": job.pages
        }
    if not data:
        raise HTTPException(status_code=404, detail="Crawl data not found.")

    pages = data.get("pages", [])
    seed_url = data.get("seed_url", "Website")
    safe_domain = urlparse(seed_url).netloc.replace("www.", "") or "export"
    total_pages = len(pages)

    # Build clean HTML template for printing
    html_items = []
    for idx, p in enumerate(pages, start=1):
        url = p.get("url", "")
        content = p.get("content", "").replace("<", "&lt;").replace(">", "&gt;")
        html_items.append(f"""
        <div class="page-card">
            <div class="page-meta">
                <span class="page-num">#{idx}</span>
                <a href="{url}" class="page-url" target="_blank">{url}</a>
            </div>
            <div class="page-content">{content}</div>
        </div>
        """)

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Axom AI Dataset - {safe_domain}</title>
    <style>
        @page {{
            margin: 18mm 15mm 18mm 15mm;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Nirmala UI", Arial, sans-serif;
            color: #1e293b;
            line-height: 1.6;
            background: #ffffff;
            margin: 0;
            padding: 0;
        }}
        .header {{
            border-bottom: 2px solid #3b82f6;
            padding-bottom: 12px;
            margin-bottom: 24px;
        }}
        .header h1 {{
            margin: 0 0 6px 0;
            font-size: 20px;
            color: #0f172a;
        }}
        .header .meta {{
            font-size: 12px;
            color: #64748b;
        }}
        .page-card {{
            margin-bottom: 24px;
            padding-bottom: 18px;
            border-bottom: 1px solid #e2e8f0;
            page-break-inside: avoid;
        }}
        .page-meta {{
            display: flex;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 8px;
        }}
        .page-num {{
            background: #eff6ff;
            color: #2563eb;
            font-weight: 700;
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid #bfdbfe;
        }}
        .page-url {{
            font-size: 13px;
            font-weight: 600;
            color: #2563eb;
            text-decoration: none;
            word-break: break-all;
        }}
        .page-content {{
            font-size: 13px;
            color: #334155;
            white-space: pre-wrap;
            word-break: break-word;
            line-height: 1.7;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Axom AI — Extracted Web Content</h1>
        <div class="meta">
            Target: <strong>{seed_url}</strong> | Total Pages: <strong>{total_pages}</strong>
        </div>
    </div>
    {"".join(html_items)}
</body>
</html>"""

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(full_html)
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"}
            )
            await browser.close()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{job_id}_{safe_domain}.pdf"'}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")


@app.delete("/api/crawl/job/{job_id}")
async def delete_crawl_job(job_id: str):
    """
    Deletes the saved JSON dataset file from disk and deletes its vectors from ChromaDB.
    """
    if not job_id or job_id == "null":
        raise HTTPException(status_code=400, detail="Invalid job ID.")

    deleted_file = False
    if os.path.exists(storage_manager.output_dir):
        for filename in os.listdir(storage_manager.output_dir):
            if (filename.startswith(f"{job_id}_") or filename == f"{job_id}.json") and filename.endswith(".json"):
                filepath = os.path.join(storage_manager.output_dir, filename)
                try:
                    os.remove(filepath)
                    deleted_file = True
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

    # Remove from active_jobs if present
    in_active = False
    if job_id in active_jobs:
        job = active_jobs[job_id]
        if job.status in ("queued", "running"):
            job.cancel()
        del active_jobs[job_id]
        in_active = True

    # Delete vectors from Vector DB
    deleted_vectors = vector_store.delete_job_vectors(job_id)

    if deleted_file or in_active or deleted_vectors > 0:
        return {
            "job_id": job_id,
            "message": "Dataset and vectors deleted successfully.",
            "deleted_vectors": deleted_vectors
        }
    raise HTTPException(status_code=404, detail="Dataset file not found.")


@app.post("/api/rag/reset")
async def reset_vector_store():
    """
    Resets the ChromaDB vector database, deleting all indexed document chunks.
    """
    deleted_count = vector_store.reset_collection()
    return {
        "message": "Vector database reset successfully.",
        "deleted_vectors": deleted_count,
        "total_vectors": 0
    }


# --- RAG Endpoints ---

@app.post("/api/rag/index/{job_id}", response_model=RAGIndexResponse)
async def index_job_to_rag(job_id: str):
    """
    Takes a crawled JSON dataset, chunks it, generates vector embeddings,
    and stores it in the local ChromaDB vector store.
    """
    data = storage_manager.get_crawl_job(job_id)
    if not data and job_id in active_jobs:
        job = active_jobs[job_id]
        data = {
            "job_id": job.job_id,
            "seed_url": job.seed_url,
            "pages": job.pages
        }

    if not data:
        raise HTTPException(status_code=404, detail=f"Crawl job '{job_id}' not found.")

    result = vector_store.index_crawl_job(data)
    return RAGIndexResponse(
        job_id=job_id,
        indexed_chunks=result["indexed_chunks"],
        total_vectors_in_db=result["total_vectors_in_db"],
        message=result["message"]
    )


@app.post("/api/rag/query", response_model=RAGQueryResponse)
async def query_rag(request: RAGQueryRequest):
    """
    Performs semantic search across all indexed web content.
    Returns the most relevant text passages with similarity scores and page URLs.
    """
    matches = vector_store.query(request.query, n_results=request.top_k)
    return RAGQueryResponse(
        query=request.query,
        total_matches=len(matches),
        matches=matches
    )


@app.get("/api/rag/stats")
async def rag_stats():
    """
    Returns Vector Store statistics.
    """
    return vector_store.get_stats()


@app.post("/api/rag/chat", response_model=RAGChatResponse)
async def chat_rag(request: RAGChatRequest):
    """
    RAG Q&A: Performs semantic vector retrieval and synthesizes a direct answer
    using Gemini, local Ollama, or grounded synthesizer.
    """
    chunks = vector_store.query(request.question, n_results=request.top_k)

    result = await llm_manager.generate_answer(
        question=request.question,
        context_chunks=chunks,
        provider=request.provider,
        api_key=request.api_key
    )

    return RAGChatResponse(
        question=request.question,
        answer=result["answer"],
        sources=result["sources"],
        provider_used=result["provider_used"]
    )


# --- Schedule Endpoints ---

@app.get("/api/schedule/list")
async def list_schedules():
    return {"schedules": get_all_schedules()}


@app.post("/api/schedule/toggle")
async def toggle_schedule(request: Request):
    body = await request.json()
    seed_url = body.get("seed_url", "").strip()
    enabled = body.get("enabled", True)
    if not seed_url:
        raise HTTPException(status_code=400, detail="seed_url required")
    ok = set_schedule_enabled(seed_url, enabled)
    if not ok:
        register_crawl(seed_url, enabled=enabled)
    return {"seed_url": seed_url, "enabled": enabled}


@app.delete("/api/schedule/{seed_url:path}")
async def delete_schedule(seed_url: str):
    remove_schedule(seed_url)
    return {"message": "Schedule removed", "seed_url": seed_url}

