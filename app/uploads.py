# -*- coding: utf-8 -*-
"""Uploaded documents (images / PDFs) on their way to Axom AI.

    upload  ->  text is read (app/doc_ocr.py)  ->  status "review"  ->  an admin reads / corrects the text and approves
            ->  saved like a crawl (so it shows in "Crawled Datasets") and sent to the chat the same way as a crawled site.

Nothing is sent to the chat before the approval. Everything lives in UPLOAD_DIR/<id>/ (the original files, the page pictures
and meta.json), so a restart loses nothing. At most DAILY_PAGES pages a day go to the paid readers (Gemini / OpenAI); after that
pages are read by Tesseract only and flagged for review. The limit can be changed with the SystemSetting "bot_upload_daily_pages".
"""
import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app import doc_ocr

log = logging.getLogger("axomai.uploads")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads"))
DEFAULT_DAILY_PAGES = 500
MAX_FILES = 30                      # pictures in one upload (a multi-page phone scan)
DOC_HOST = "documents.aiaxom.co.in"
ID_RE = re.compile(r"^[a-f0-9]{12}$")
_lock = asyncio.Lock()              # one document is read at a time: bounded memory and API rate


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def doc_dir(uid: str, base: Optional[str] = None) -> str:
    if not ID_RE.match(uid):
        raise ValueError("bad id")
    return os.path.join(base or UPLOAD_DIR, uid)


def read_meta(uid: str, base: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(doc_dir(uid, base), "meta.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_meta(meta: Dict[str, Any], base: Optional[str] = None) -> None:
    d = doc_dir(meta["id"], base)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "meta.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, "meta.json"))


def list_uploads(base: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    root = base or UPLOAD_DIR
    if not os.path.isdir(root):
        return out
    for uid in os.listdir(root):
        if not ID_RE.match(uid):
            continue
        m = read_meta(uid, base)
        if m:
            out.append({k: m.get(k) for k in ("id", "name", "kind", "size", "uploader", "created_at", "status", "error", "page_count", "job_id")}
                        | {"review_pages": sum(1 for p in m.get("pages", []) if p.get("review")), "engines": sorted({p.get("engine") for p in m.get("pages", []) if p.get("engine")})})
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


# ---------------------------------------------------------------- daily limit
def daily_limit() -> int:
    try:
        from app.auth import get_db_conn
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM superadmin_systemsetting WHERE key = 'bot_upload_daily_pages'")
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if row:
            return max(0, int(row[0]))
    except Exception:
        pass
    return DEFAULT_DAILY_PAGES


def _usage_file(base: Optional[str]) -> str:
    return os.path.join(base or UPLOAD_DIR, "_usage.json")


def pages_used_today(base: Optional[str] = None) -> int:
    try:
        with open(_usage_file(base), "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0))
    except (OSError, ValueError):
        return 0


def count_paid_page(base: Optional[str] = None) -> None:
    os.makedirs(base or UPLOAD_DIR, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = pages_used_today(base) + 1
    with open(_usage_file(base), "w", encoding="utf-8") as f:
        json.dump({day: used}, f)


# ---------------------------------------------------------------- create
class UploadError(Exception):
    """The upload is refused; the message is shown to the user."""


def create_upload(files: List[Tuple[str, bytes]], uploader: str, base: Optional[str] = None) -> Dict[str, Any]:
    """Checks the files (real type, size) and stores them. `files`: [(file name, bytes)]. Returns the new meta (status "processing")."""
    if not files:
        raise UploadError("Choose a file first.")
    if len(files) > MAX_FILES:
        raise UploadError("At most %d pictures in one upload." % MAX_FILES)
    kinds = []
    for name, data in files:
        if not data:
            raise UploadError("%s is empty." % (name or "A file"))
        if len(data) > doc_ocr.MAX_BYTES:
            raise UploadError("%s is bigger than 20 MB." % (name or "A file"))
        kind = doc_ocr.detect_kind(data)
        if not kind:
            raise UploadError("%s is not an image (PNG, JPG, WEBP) or a PDF." % (name or "A file"))
        kinds.append(kind)
    if "pdf" in kinds and len(files) > 1:
        raise UploadError("Upload one PDF at a time (or several pictures together).")
    uid = uuid.uuid4().hex[:12]
    d = doc_dir(uid, base)
    os.makedirs(d, exist_ok=True)
    stored = []
    for i, ((name, data), kind) in enumerate(zip(files, kinds), 1):
        ext = "pdf" if kind == "pdf" else doc_ocr.EXT[kind]
        fn = "orig%d.%s" % (i, ext)
        with open(os.path.join(d, fn), "wb") as f:
            f.write(data)
        stored.append({"file": fn, "name": re.sub(r"[^\w .()-]", "_", os.path.basename(name or fn))[:120], "kind": kind})
    title = stored[0]["name"] if len(stored) == 1 else "%s (+%d more)" % (stored[0]["name"], len(stored) - 1)
    meta = {"id": uid, "name": title, "kind": "pdf" if kinds[0] == "pdf" else "images", "size": sum(len(b) for _, b in files),
            "uploader": uploader, "created_at": now(), "status": "processing", "error": "", "files": stored, "pages": [],
            "page_count": 0, "job_id": ""}
    write_meta(meta, base)
    return meta


# ---------------------------------------------------------------- read the text
async def process_upload(uid: str, base: Optional[str] = None, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """Reads every page of an upload and moves it to "review". Never raises: a failure is stored as status "failed"."""
    async with _lock:
        meta = read_meta(uid, base)
        if not meta or meta.get("status") != "processing":
            return meta or {}
        own = client is None
        client = client or httpx.AsyncClient()
        d = doc_dir(uid, base)
        try:
            pages: List[Dict[str, Any]] = []
            limit = daily_limit()

            async def read_picture(path: str, kind: str) -> Dict[str, Any]:
                paid = pages_used_today(base) < limit
                if paid:
                    count_paid_page(base)
                return await doc_ocr.read_image(path, kind, client, paid_ok=paid)

            if meta["kind"] == "pdf":
                path = os.path.join(d, meta["files"][0]["file"])
                total = await asyncio.to_thread(doc_ocr.pdf_page_count, path)
                if total < 1:
                    raise UploadError("This PDF has no pages.")
                if total > doc_ocr.MAX_PDF_PAGES:
                    raise UploadError("This PDF has %d pages; the limit is %d." % (total, doc_ocr.MAX_PDF_PAGES))
                for n in range(1, total + 1):
                    layer = doc_ocr.clean_text(await asyncio.to_thread(doc_ocr.pdf_text_layer, path, n))
                    if len(layer) >= doc_ocr.TEXT_LAYER_MIN_CHARS:
                        pages.append({"n": n, "text": layer, "engine": "pdf-text", "review": False, "reason": "", "image": ""})
                    else:
                        img = await asyncio.to_thread(doc_ocr.pdf_render, path, n, os.path.join(d, "p%d" % n))
                        r = await read_picture(img, "jpeg")
                        pages.append({"n": n, "text": r["text"], "engine": r["engine"], "review": r["review"], "reason": r["reason"], "image": os.path.basename(img)})
                    meta["pages"], meta["page_count"] = pages, total
                    write_meta(meta, base)
            else:
                for n, f in enumerate(meta["files"], 1):
                    r = await read_picture(os.path.join(d, f["file"]), f["kind"])
                    pages.append({"n": n, "text": r["text"], "engine": r["engine"], "review": r["review"], "reason": r["reason"], "image": f["file"]})
                    meta["pages"], meta["page_count"] = pages, len(meta["files"])
                    write_meta(meta, base)
            meta["status"] = "review" if any(p["text"].strip() for p in pages) else "failed"
            if meta["status"] == "failed":
                meta["error"] = "No text could be read from this file."
        except (UploadError, doc_ocr.EngineUnavailable) as e:
            meta["status"], meta["error"] = "failed", str(e)
        except Exception as e:      # whatever it is, the upload must not stay "processing" forever
            log.exception("Reading upload %s failed", uid)
            meta["status"], meta["error"] = "failed", "Reading failed (%s)." % type(e).__name__
        finally:
            if own:
                await client.aclose()
        write_meta(meta, base)
        return meta


def resume_unfinished(base: Optional[str] = None) -> List[str]:
    """Uploads that were still "processing" when the server stopped: they are read again from their originals."""
    return [u["id"] for u in list_uploads(base) if u.get("status") == "processing"]


# ---------------------------------------------------------------- review / approve
def edit_page(uid: str, n: int, text: str, base: Optional[str] = None) -> Dict[str, Any]:
    meta = read_meta(uid, base)
    if not meta or meta["status"] != "review":
        raise UploadError("This upload is not waiting for review.")
    for p in meta["pages"]:
        if p["n"] == n:
            p["text"] = doc_ocr.clean_text(text)
            p["edited"] = True
            p["review"] = False
            write_meta(meta, base)
            return meta
    raise UploadError("No such page.")


def build_pages(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The pages of the upload as crawl pages (what the chat import takes). Empty pages are left out."""
    out = []
    for p in meta["pages"]:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        out.append({"url": "https://%s/%s#p%d" % (DOC_HOST, meta["id"], p["n"]),
                    "title": "%s - page %d" % (meta["name"], p["n"]) if len(meta["pages"]) > 1 else meta["name"],
                    "content": text, "word_count": len(text.split()), "change_status": "new"})
    return out


def seed_url(meta: Dict[str, Any]) -> str:
    return "https://%s/%s" % (DOC_HOST, meta["id"])


def mark(uid: str, status: str, user: str, base: Optional[str] = None, job_id: str = "") -> Dict[str, Any]:
    meta = read_meta(uid, base)
    if not meta or meta["status"] not in ("review", "approved"):
        raise UploadError("This upload is not waiting for review.")
    meta["status"] = status
    meta["reviewed_by"], meta["reviewed_at"] = user, now()
    if job_id:
        meta["job_id"] = job_id
    write_meta(meta, base)
    return meta


def delete_upload(uid: str, base: Optional[str] = None) -> bool:
    d = doc_dir(uid, base)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False
