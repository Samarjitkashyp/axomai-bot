import os
import io
import csv
import json
import uuid
import secrets
import asyncio
import logging
import httpx
from datetime import datetime
from typing import Dict, List, Any
from urllib.parse import urlparse
from html import escape as html_escape
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import (
    authenticate_user, create_session_cookie, get_current_user,
    COOKIE_NAME, PUBLIC_PATHS, get_db_conn,
)
from app.login_guard import LoginGuard, client_ip as _login_ip, sanitize as _login_clean
from app.login_page import render_login
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
from crawler.frontier import URLFrontier
from app.export import export_pages
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


def _admin_panel_user(request: Request):
    """The Axom AI admin panel (Document Verify) talks to the upload API with the shared bot token (the same one the chat import uses)
    and names the admin in X-Admin-User. Only /api/upload* accepts this."""
    import hmac
    expected = os.getenv("BOT_IMPORT_TOKEN", "")
    given = request.headers.get("X-Bot-Token", "")
    if expected and given and hmac.compare_digest(given.encode(), expected.encode()):
        return {"id": 0, "username": (request.headers.get("X-Admin-User") or "admin-panel")[:60], "is_superuser": True}
    return None


def _admin_only(request: Request):
    """Approving, editing, rejecting, deleting uploads is for the admin panel only (not for every bot user)."""
    if not getattr(request.state, "via_admin_panel", False):
        raise HTTPException(status_code=403, detail="Documents are approved in the Axom AI admin panel (Document Verify).")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path.startswith("/docs") or path.startswith("/openapi") or path in PUBLIC_PATHS or path.startswith("/api/mcp"):
            return await call_next(request)
        request.state.via_admin_panel = False
        if path.startswith("/api/upload"):
            panel = _admin_panel_user(request)
            if panel:
                request.state.user = panel
                request.state.via_admin_panel = True
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


CSRF_COOKIE = "axomai_bot_csrf"
login_guard = LoginGuard(get_db_conn)


def _login_response(html: str, status: int = 200, csrf: str = ""):
    """A login page: never cached, not framed, with the visitor's CSRF cookie (the same value is in the form)."""
    resp = HTMLResponse(html, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if csrf:
        resp.set_cookie(CSRF_COOKIE, csrf, max_age=3600, httponly=True, secure=True, samesite="strict")
    return resp


def _request_ip(request: Request) -> str:
    return _login_ip(request.headers, request.client.host if request.client else "")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=302)
    ip = _request_ip(request)
    csrf = request.cookies.get(CSRF_COOKIE) or uuid.uuid4().hex + uuid.uuid4().hex
    try:
        lock = await asyncio.to_thread(login_guard.active_lockout, ip, "")
    except Exception:
        lock = {"is_locked": False}
    if lock.get("is_locked"):
        msg = (f"Security Lockout Active: Too many failed login attempts. This IP address is locked for 24 hours "
               f"({lock['hours']}h {lock['mins']}m remaining). For security, unauthorized brute force requests are blocked.")
        return _login_response(render_login(error=msg, is_locked=True, lockout=lock, remaining=0, client_ip=ip, csrf=csrf), csrf=csrf)
    return _login_response(render_login(client_ip=ip, csrf=csrf), csrf=csrf)


@app.post("/login")
async def login_submit(request: Request, username: str = Form(""), password: str = Form(""), csrf: str = Form(""), remember: str = Form("")):
    ip = _request_ip(request)
    ua = request.headers.get("user-agent", "")
    cookie_csrf = request.cookies.get(CSRF_COOKIE) or ""
    if not cookie_csrf or not csrf or not secrets.compare_digest(cookie_csrf, csrf):
        # the page was opened too long ago, or the form came from somewhere else: nothing is checked, nothing is counted
        fresh = uuid.uuid4().hex + uuid.uuid4().hex
        return _login_response(render_login(error="This page expired. Please try again.", client_ip=ip, csrf=fresh), 403, fresh)

    u = _login_clean(username, 150)
    p = _login_clean(password, 256)

    def locked_page(lock, who):
        h, m = lock["hours"], lock["mins"]
        if who == "user":
            msg = f"Security Lockout Active: Account '{u}' is locked for 24 hours ({h}h {m}m remaining). Contact system administrator."
        else:
            msg = ("Extreme Security Lockout: 3 consecutive failed login attempts detected. Your IP address and account have been LOCKED FOR 24 HOURS. "
                   "All further attempts are automatically blocked.") if who == "new" else (
                f"Security Lockout Active: Too many failed login attempts. This IP address is locked for 24 hours ({h}h {m}m remaining).")
        return _login_response(render_login(error=msg, is_locked=True, lockout=lock, remaining=0, client_ip=ip, last_username=u, csrf=cookie_csrf), 429, cookie_csrf)

    try:
        lock = await asyncio.to_thread(login_guard.active_lockout, ip, u)
    except Exception:
        return _login_response(render_login(error="Sign-in is temporarily unavailable. Please try again in a minute.", client_ip=ip, last_username=u, csrf=cookie_csrf), 503, cookie_csrf)
    if lock.get("is_locked"):
        return locked_page(lock, "user" if u else "ip")

    status, user = await asyncio.to_thread(authenticate_user, u, p) if (u and p) else ("bad", None)

    if status == "error":
        return _login_response(render_login(error="Sign-in is temporarily unavailable. Please try again in a minute.", client_ip=ip, last_username=u, csrf=cookie_csrf), 503, cookie_csrf)

    if status != "ok":
        denied = status == "denied"
        await asyncio.to_thread(login_guard.audit, "BOT_LOGIN_DENIED" if denied else "BOT_LOGIN_FAILED", f"Username: {u}",
                                "Password right, no bot access" if denied else "Invalid credentials provided", ip)
        fail = await asyncio.to_thread(login_guard.record_failed, ip, u, ua)
        if fail.get("is_locked"):
            return locked_page({"hours": 24, "mins": 0}, "new")
        rem = fail.get("remaining_attempts", 1)
        s_ = "s" if rem != 1 else ""
        if denied:
            msg = f"Unauthorized: this account has no access to the Crawler Bot. ({rem} attempt{s_} remaining)"
        else:
            msg = (f"Invalid credentials. Warning: {rem} attempt{s_} remaining before a mandatory 24-HOUR SECURITY LOCKOUT.")
        return _login_response(render_login(error=msg, remaining=rem, client_ip=ip, last_username=u, csrf=cookie_csrf), 401, cookie_csrf)

    await asyncio.to_thread(login_guard.clear_failed, ip, u)
    await asyncio.to_thread(login_guard.audit, "BOT_LOGIN_SUCCESS", f"User: {user['username']}", "Logged into the Crawler Bot", ip)
    response = RedirectResponse("/", status_code=302)
    kwargs = {"max_age": 86400 * 7} if remember else {}              # "remember" off: the cookie lasts until the browser is closed
    response.set_cookie(COOKIE_NAME, create_session_cookie(user), httponly=True, secure=True, samesite="lax", **kwargs)
    response.delete_cookie(CSRF_COOKIE)
    return response


@app.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)
    if user:
        await asyncio.to_thread(login_guard.audit, "BOT_LOGOUT", f"User: {user.get('username', '')}", "", _request_ip(request))
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
            html = f.read()
        # the CSS / JS links carry the file's change time, so a browser or Cloudflare never keeps showing an old design after a deploy
        def _versioned(m):
            try:
                return '%s?v=%d"' % (m.group(1), int(os.path.getmtime(os.path.join(STATIC_DIR, m.group(2)))))
            except OSError:
                return m.group(0)
        import re as _re
        html = _re.sub(r'(/static/([\w.-]+\.(?:css|js)))"', _versioned, html)
        return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>Axom AI Crawler & RAG API is running! Visit /docs for Swagger UI.</h1>")


_import_logger = logging.getLogger("axomai.import")

CHAT_IMPORT_URL = os.getenv("CHAT_IMPORT_URL", "http://127.0.0.1:8000/api/import-crawl/")
BOT_IMPORT_TOKEN = os.getenv("BOT_IMPORT_TOKEN", "")


def _import_ok(outcome) -> bool:
    totals = (outcome or {}).get("totals") or {}
    return not (totals.get("failed_batches") or totals.get("aborted"))


async def _push_to_chat(job_id: str, seed_url: str):
    """Sends a saved crawl to the chat. Returns True when it arrived (or nothing was left to send), False when it did not."""
    if not BOT_IMPORT_TOKEN:
        _import_logger.warning("BOT_IMPORT_TOKEN not set — skipping chat import")
        return False
    data = storage_manager.get_crawl_job(job_id)
    if not data:
        return False
    pages = data.get("pages", [])
    if not pages:
        return True
    # AXOMAI-BATCH: batches of 200 pages with retries (a single huge request could time out and lose the whole crawl)
    from app.chat_import import push_pages

    async def _push(batch):
        return await push_pages(CHAT_IMPORT_URL, BOT_IMPORT_TOKEN, seed_url, job_id, batch)

    # AXOMAI-QA: passages + one AI-written question each (any site); falls back to whole pages when anything is not available
    from app.qa_chunks import qa_enabled, push_with_passages
    if qa_enabled(seed_url):
        from app.auth import get_db_conn
        outcome = await push_with_passages(_push, seed_url, job_id, pages, get_db_conn)
        _import_logger.info("Chat import %s: %s", seed_url, {k: v for k, v in outcome.items() if k != "totals"})
        return _import_ok(outcome)
    return _import_ok({"totals": await _push(pages)})


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
    Generates and streams an Excel-compatible CSV file with two columns: the page name and its clean content (no URL, links or buttons).
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
    # Strictly only 2 columns: Page (name) and Content
    writer.writerow(["Page", "Content"])

    for item in export_pages(pages):
        writer.writerow([item["page"], item["content"]])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    safe_domain = urlparse(data.get("seed_url", "crawl")).netloc.replace("www.", "") or "export"
    filename = f"{job_id}_{safe_domain}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/crawl/export/{job_id}/json")
async def export_crawl_json(job_id: str):
    """
    Content-only JSON: a list of {"page": name, "content": text}. The full dataset (links, stats) is still at /api/crawl/download/{job_id}.
    """
    data = storage_manager.get_crawl_job(job_id)
    if not data and job_id in active_jobs:
        data = {"seed_url": active_jobs[job_id].seed_url, "pages": active_jobs[job_id].pages}
    if not data:
        raise HTTPException(status_code=404, detail="Crawl data not found.")
    safe_domain = urlparse(data.get("seed_url", "crawl")).netloc.replace("www.", "") or "export"
    body = json.dumps(export_pages(data.get("pages", [])), ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{job_id}_{safe_domain}_content.json"'}
    )


@app.get("/api/crawl/export/{job_id}/pdf")
async def export_crawl_pdf(job_id: str):
    """
    Generates and streams a clean, printable PDF: for every page its name as a heading and its clean content (no URL, links or buttons).
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

    # Build clean HTML template for printing
    html_items = []
    for item in export_pages(pages):
        name = html_escape(item["page"])
        content = html_escape(item["content"])
        html_items.append(f"""
        <div class="page-card">
            <h2 class="page-name">{name}</h2>
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
        .page-card {{
            margin-bottom: 28px;
        }}
        .page-name {{
            font-size: 17px;
            font-weight: 700;
            color: #0f172a;
            margin: 0 0 8px 0;
            break-after: avoid;
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

# --- Document uploads (images / PDFs -> text -> admin approval -> Axom AI chat) ---

from fastapi import UploadFile, File
from app import uploads as upl
from app import doc_ocr

_upload_tasks: set = set()


def _spawn_read(uid: str):
    task = asyncio.create_task(upl.process_upload(uid))
    _upload_tasks.add(task)
    task.add_done_callback(_upload_tasks.discard)


@app.on_event("startup")
async def resume_uploads():
    for uid in upl.resume_unfinished():
        _spawn_read(uid)


def _upload_or_404(uid: str) -> Dict[str, Any]:
    try:
        meta = upl.read_meta(uid)
    except ValueError:
        meta = None
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return meta


@app.post("/api/upload")
async def upload_documents(request: Request, files: List[UploadFile] = File(...)):
    """One PDF, or one or more pictures (a phone scan is several pictures = several pages of one document)."""
    blobs = []
    for f in files:
        data = await f.read(doc_ocr.MAX_PDF_BYTES + 1)
        blobs.append((f.filename or "scan", data))
    try:
        meta = upl.create_upload(blobs, getattr(request.state, "user", {}).get("username", ""))
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _spawn_read(meta["id"])
    return {"id": meta["id"], "status": meta["status"], "name": meta["name"]}


@app.post("/api/upload/begin")
async def upload_begin(request: Request):
    """Big files (PDFs up to 150 MB) are sent in 8 MB pieces: begin -> /chunk/<sid>/<n> ... -> /finish/<sid>."""
    body = await request.json()
    try:
        return upl.begin_chunked(str(body.get("name") or "file"), body.get("size"))
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/upload/chunk/{sid}/{index}")
async def upload_chunk(sid: str, index: int, request: Request):
    data = await request.body()
    try:
        stored = upl.save_chunk(sid, index, data)
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "stored": stored}


@app.post("/api/upload/finish/{sid}")
async def upload_finish(sid: str, request: Request):
    try:
        meta = await asyncio.to_thread(upl.finish_chunked, sid, getattr(request.state, "user", {}).get("username", ""))
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _spawn_read(meta["id"])
    return {"id": meta["id"], "status": meta["status"], "name": meta["name"]}


@app.get("/api/upload/list")
async def list_uploads():
    return {"uploads": upl.list_uploads(), "pages_used_today": upl.pages_used_today(), "daily_limit": upl.daily_limit(),
            "ocr": {"tesseract": doc_ocr.tesseract_available(), "poppler": doc_ocr.poppler_available(),
                    "gemini": bool(doc_ocr._env_key("GEMINI_API_KEY")), "openai": bool(doc_ocr._env_key("OPENAI_API_KEY"))}}


@app.get("/api/upload/{uid}")
async def get_upload(uid: str):
    meta = _upload_or_404(uid)
    return {k: v for k, v in meta.items() if k != "files"}


@app.get("/api/upload/{uid}/image/{name}")
async def upload_image(uid: str, name: str):
    meta = _upload_or_404(uid)
    if not any(p.get("image") == name for p in meta["pages"]):
        raise HTTPException(status_code=404, detail="No such picture.")
    path = os.path.join(upl.doc_dir(uid), name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No such picture.")
    return FileResponse(path)


@app.put("/api/upload/{uid}/page/{n}")
async def edit_upload_page(uid: str, n: int, request: Request):
    _admin_only(request)
    body = await request.json()
    text = body.get("text")
    if not isinstance(text, str) or len(text) > 200000:
        raise HTTPException(status_code=400, detail="Text is missing or too long.")
    try:
        meta = upl.edit_page(uid, n, text)
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    page = next(p for p in meta["pages"] if p["n"] == n)
    return {"ok": True, "page": page}


async def _approve_and_send(uid: str):
    meta = upl.read_meta(uid)
    pages = upl.build_pages(meta)
    job_id = "doc-" + uid
    seed = upl.seed_url(meta)
    now_iso = datetime.now().isoformat()
    storage_manager.save_crawl_job(job_id, seed, "completed", pages,
                                   {"started_at": meta["created_at"], "completed_at": now_iso, "source": "upload",
                                    "file_name": meta["name"], "engines": sorted({p.get("engine") for p in meta["pages"]})})
    ok = await _push_to_chat(job_id, seed)
    upl.set_send_status(uid, bool(ok))


@app.post("/api/upload/{uid}/approve")
async def approve_upload(uid: str, request: Request, background_tasks: BackgroundTasks):
    _admin_only(request)
    meta = _upload_or_404(uid)
    if meta["status"] != "review":
        raise HTTPException(status_code=400, detail="This upload is not waiting for review.")
    if not upl.build_pages(meta):
        raise HTTPException(status_code=400, detail="There is no text to send.")
    user = getattr(request.state, "user", {}).get("username", "")
    upl.mark(uid, "approved", user, job_id="doc-" + uid)
    background_tasks.add_task(_approve_and_send, uid)
    return {"ok": True, "job_id": "doc-" + uid}


@app.post("/api/upload/{uid}/resend")
async def resend_upload(uid: str, request: Request, background_tasks: BackgroundTasks):
    _admin_only(request)
    meta = _upload_or_404(uid)
    if meta["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved uploads can be sent again.")
    upl.mark(uid, "approved", getattr(request.state, "user", {}).get("username", ""), job_id="doc-" + uid)
    background_tasks.add_task(_approve_and_send, uid)
    return {"ok": True}


@app.post("/api/upload/{uid}/reread")
async def reread_upload(uid: str, request: Request):
    _admin_only(request)
    _upload_or_404(uid)
    try:
        meta = upl.reread_upload(uid, getattr(request.state, "user", {}).get("username", ""))
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _spawn_read(meta["id"])
    return {"ok": True}


@app.post("/api/upload/{uid}/reject")
async def reject_upload(uid: str, request: Request):
    _admin_only(request)
    _upload_or_404(uid)
    try:
        upl.mark(uid, "rejected", getattr(request.state, "user", {}).get("username", ""))
    except upl.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.delete("/api/upload/{uid}")
async def delete_upload(uid: str, request: Request):
    _admin_only(request)
    meta = _upload_or_404(uid)
    if meta["status"] == "processing":
        raise HTTPException(status_code=400, detail="It is still being read.")
    upl.delete_upload(uid)
    return {"ok": True}


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


# --- Model Context Protocol (MCP) Server Endpoints ---
# REA Enhancement: JSON-RPC 2.0 MCP interface compatible with Cursor, Claude Desktop, and AI subagents

@app.get("/api/mcp")
async def mcp_server_info():
    """
    Model Context Protocol (MCP) Server Discovery & Health.
    Provides server capabilities, tool specifications, and integration instructions.
    """
    return {
        "name": "axomai-bot-mcp",
        "version": "1.0.0",
        "description": "Axom AI Intelligent Crawler & Semantic RAG MCP Server",
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {
                "crawl_website": {
                    "description": "Trigger an automated web crawl on any website with UTF-8 multilingual extraction (Assamese, Hindi, English).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Target website seed URL (e.g. https://example.com)"},
                            "max_pages": {"type": "integer", "default": 10, "description": "Maximum pages to crawl"},
                            "max_depth": {"type": "integer", "default": 2, "description": "Maximum link depth"},
                            "render_js": {"type": "boolean", "default": False, "description": "Whether to render dynamic JavaScript via Playwright"}
                        },
                        "required": ["url"]
                    }
                },
                "get_crawl_status": {
                    "description": "Fetch real-time crawling status, progress, logs, and pages for a job ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string", "description": "The job ID returned by crawl_website"}
                        },
                        "required": ["job_id"]
                    }
                },
                "search_knowledge_base": {
                    "description": "Perform semantic vector search across all indexed web content in ChromaDB.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query or natural language question"},
                            "top_k": {"type": "integer", "default": 5, "description": "Number of top matching chunks to return"}
                        },
                        "required": ["query"]
                    }
                },
                "discover_sitemap": {
                    "description": "Probes and extracts page URLs from the website's XML sitemaps (/sitemap.xml, /wp-sitemap.xml).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Target website URL"}
                        },
                        "required": ["url"]
                    }
                }
            }
        },
        "endpoints": {
            "mcp_rpc": "/api/mcp",
            "documentation": "/docs"
        }
    }


@app.post("/api/mcp")
async def mcp_rpc_handler(request: Request, background_tasks: BackgroundTasks):
    """
    Model Context Protocol (MCP) JSON-RPC 2.0 Handler.
    Compatible with Claude Desktop, Cursor, and any MCP Client.
    """
    token = os.getenv("BOT_IMPORT_TOKEN", "").strip()
    if token:
        auth_header = request.headers.get("Authorization", "")
        bot_token_header = request.headers.get("X-Bot-Token", "")
        bearer_token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""
        user = get_current_user(request)
        is_authenticated = bool(user) or (
            secrets.compare_digest(bot_token_header, token) if bot_token_header else False
        ) or (
            secrets.compare_digest(bearer_token, token) if bearer_token else False
        )
        if not is_authenticated:
            return JSONResponse(
                status_code=401,
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Unauthorized. Provide valid X-Bot-Token or Authorization Bearer header."},
                    "id": None
                }
            )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error. Invalid JSON was received."}, "id": None}
        )

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "axomai-bot-mcp",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "crawl_website",
                        "description": "Trigger an automated web crawl on any website with UTF-8 multilingual extraction (Assamese, Hindi, English).",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "Target website seed URL (e.g. https://example.com)"},
                                "max_pages": {"type": "integer", "default": 10, "description": "Maximum pages to crawl"},
                                "max_depth": {"type": "integer", "default": 2, "description": "Maximum link depth"},
                                "render_js": {"type": "boolean", "default": False, "description": "Whether to render dynamic JavaScript via Playwright"}
                            },
                            "required": ["url"]
                        }
                    },
                    {
                        "name": "get_crawl_status",
                        "description": "Fetch real-time crawling status, progress, logs, and pages for a job ID.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "job_id": {"type": "string", "description": "The job ID returned by crawl_website"}
                            },
                            "required": ["job_id"]
                        }
                    },
                    {
                        "name": "search_knowledge_base",
                        "description": "Perform semantic vector search across all indexed web content in ChromaDB.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Search query or natural language question"},
                                "top_k": {"type": "integer", "default": 5, "description": "Number of top matching chunks to return"}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "discover_sitemap",
                        "description": "Probes and extracts page URLs from the website's XML sitemaps (/sitemap.xml, /wp-sitemap.xml).",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "Target website URL"}
                            },
                            "required": ["url"]
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "crawl_website":
            url = arguments.get("url", "").strip()
            if not url:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Missing required argument 'url'"}
                }
            max_pages = int(arguments.get("max_pages", 10))
            max_depth = int(arguments.get("max_depth", 2))
            render_js = bool(arguments.get("render_js", False))

            job_id = f"job_{uuid.uuid4().hex[:8]}"
            job = CrawlerJob(
                job_id=job_id,
                seed_url=url,
                max_pages=max_pages,
                max_depth=max_depth,
                crawl_delay=0.5,
                render_js=render_js,
                force_recrawl=False,
                include_subdomains=False
            )
            active_jobs[job_id] = job
            engine = CrawlerEngine(job=job, storage_manager=storage_manager)

            async def _crawl_and_register():
                await engine.run()
                if job.status == "completed":
                    register_crawl(url)
                    await _push_to_chat(job_id, url)

            background_tasks.add_task(_crawl_and_register)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Successfully initiated crawl for {url}.\nJob ID: {job_id}\nStatus: queued\nTrack progress with get_crawl_status(job_id='{job_id}')"
                        }
                    ]
                }
            }

        elif tool_name == "get_crawl_status":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing 'job_id'"}}

            if job_id in active_jobs:
                data = active_jobs[job_id].to_dict()
            else:
                data = storage_manager.get_crawl_job(job_id)

            if not data:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"Job ID '{job_id}' not found."}]}
                }

            status = data.get("status", "unknown")
            pages_crawled = data.get("pages_crawled", len(data.get("pages", [])))
            seed_url = data.get("seed_url", "")
            summary = f"Crawl Status for {job_id} ({seed_url}):\nStatus: {status}\nPages Crawled: {pages_crawled}"
            pages = data.get("pages", []) or data.get("latest_pages", [])
            if pages:
                summary += "\nSample Pages:\n" + "\n".join([f"- {p.get('title', 'Untitled')} ({p.get('url', '')})" for p in pages[:5]])

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": summary}]}
            }

        elif tool_name == "search_knowledge_base":
            query = arguments.get("query", "").strip()
            top_k = int(arguments.get("top_k", 5))
            if not query:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing 'query'"}}

            matches = vector_store.query(query, n_results=top_k)
            if not matches:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"No indexed content found matching '{query}'."}]}
                }

            result_lines = [f"Found {len(matches)} relevant passage(s) for '{query}':\n"]
            for i, m in enumerate(matches, 1):
                result_lines.append(f"[{i}] {m.get('metadata', {}).get('title', 'Source')} ({m.get('metadata', {}).get('url', '')})")
                result_lines.append(f"Passage: {m.get('text', '')[:300]}...\n")

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": "\n".join(result_lines)}]}
            }

        elif tool_name == "discover_sitemap":
            url = arguments.get("url", "").strip()
            if not url:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing 'url'"}}

            frontier = URLFrontier(seed_url=url, max_pages=100)
            async with httpx.AsyncClient(headers={"User-Agent": CrawlerEngine.DEFAULT_USER_AGENT}, verify=False) as client:
                count = await frontier.discover_sitemap(client)

            urls = [item[0] for item in list(frontier.queue)[:10]]
            res_text = f"Discovered {count} URLs from sitemap for {url}."
            if urls:
                res_text += "\nFirst 10 sample URLs:\n" + "\n".join(f"- {u}" for u in urls)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": res_text}]}
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method or tool '{tool_name}' not found."}
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown JSON-RPC method '{method}'"}
    }


