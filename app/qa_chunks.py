# -*- coding: utf-8 -*-
"""Passages + AI questions for the Axom AI chat, built from every finished crawl (any kind of site: the crawl text is the input,
so it does not matter whether the pages were fetched as plain HTML, rendered with JavaScript, or taken from an API).

Chat answers a visitor by matching the visitor's question against stored questions. A whole page stored under its title matches
only broad questions ("tell me about X"). Here every page is cut into passages of about 100-230 words, an AI model writes ONE
natural question per passage (in the passage's own language), and each passage is stored as its own question + answer pair
(url = page url + "#c1", "#c2" ...). So "what does the SEO service include?" finds the passage about SEO, and the answer is just that passage.

Safety, in short:
  * nothing changes in Axom AI until the questions exist; a passage the AI could not do gets a plain fallback question, so no text is lost;
  * if the AI is unavailable (no key, quota, model gone), the crawl is imported exactly as before, page by page;
  * the rows that are replaced are saved to a backup file first, and if the import fails the old way is used;
  * a page that did not change since the last crawl and already has its passages is left alone (no new AI calls);
  * at most QA_MAX_PASSAGES passages per crawl; the pages beyond that are imported page by page.
Off for everything with AXOMAI_QA_CHUNKS=0, or for one site with "qa_chunks": false in its profile.
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.export import common_suffix, page_name, page_text

log = logging.getLogger("axomai.qa")

TARGET_WORDS = 160      # a passage is closed once it has this many words
MAX_WORDS = 230         # ... or when the next paragraph would take it over this
MIN_LAST_WORDS = 40     # a shorter last passage is joined to the one before it
TINY_WORDS = 12         # a passage with fewer words is not worth a row
BATCH = 8               # passages per AI call
Q_MIN, Q_MAX = 8, 220   # characters of a question
MAX_PASSAGES = int(os.getenv("QA_MAX_PASSAGES", "1500"))
DEFAULT_MODEL = os.getenv("QA_MODEL", "gemini-3.5-flash-lite")
KEY_FILE = os.getenv("QA_ENV_FILE", "/home/admin/axom_ai/.env")     # where to read GEMINI_API_KEY when the bot's own environment has none
BACKUP_DIR = os.getenv("BACKUP_DIR", "/home/admin/axom_backups")

PROMPT = """You write search questions for a website's knowledge base.
Below are text passages from the website "{site}" (page: "{name}"). For EACH passage write ONE natural question that a visitor could ask and that the passage answers directly.
Rules:
- Write the question in the same language and script as its passage (Assamese stays Assamese, Hindi stays Hindi, English stays English).
- Be specific: name the service, product, place, person or topic the passage is about. Mention "{brand}" when the passage is about the business itself (contact details, about, prices, services).
- Do not add any fact that is not in the passage. 6 to 20 words. End with a question mark.
Return only JSON, one entry per passage: [{{"id": <id>, "question": "<question>"}}]

Passages:
{passages}
"""


class QAUnavailable(Exception):
    """The AI cannot be used right now (no key, quota used up, no model that works): import the pages the old way."""


# ---------------------------------------------------------------- passages
def split_chunks(text: str) -> List[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[List[str]] = []
    cur: List[str] = []
    n = 0
    for p in paras:
        w = len(p.split())
        if cur and n + w > MAX_WORDS:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += w
        if n >= TARGET_WORDS:
            chunks.append(cur)
            cur, n = [], 0
    if cur:
        chunks.append(cur)
    if len(chunks) > 1 and sum(len(x.split()) for x in chunks[-1]) < MIN_LAST_WORDS:
        last = chunks.pop()
        chunks[-1] = chunks[-1] + last
    out = ["\n\n".join(c) for c in chunks]
    return [c for c in out if len(c.split()) >= TINY_WORDS]


def valid_question(q: Any) -> bool:
    if not isinstance(q, str):
        return False
    q = q.strip()
    return Q_MIN <= len(q) <= Q_MAX and ("?" in q or "？" in q)


def fallback_question(name: str, passage: str) -> str:
    """Used only when the AI gave no usable question: the page name and the first words of the passage."""
    return ("%s: %s" % (name, " ".join(passage.split()[:12]))).strip()[:Q_MAX]


# ---------------------------------------------------------------- Gemini
def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        from dotenv import dotenv_values
        return (dotenv_values(KEY_FILE).get("GEMINI_API_KEY") or "").strip()
    except Exception:
        return ""


class Gemini:
    """Minimal Gemini client with a list of candidate models: when one is gone (404) or its quota is used up (429), the next is tried;
    when the configured model is gone, the current flash-lite / flash models of the account are discovered."""

    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, key: str, model: str = DEFAULT_MODEL, client: Optional[httpx.AsyncClient] = None):
        self.key, self.models, self.client = key, [model], client
        self._discovered = False

    async def _discover(self):
        self._discovered = True
        try:
            r = await self.client.get(self.BASE + "/models", params={"pageSize": 200}, headers={"x-goog-api-key": self.key}, timeout=30)
            names = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        except Exception:
            return
        good = [n for n in names if re.match(r"^gemini-[\d.]+-flash(-lite)?$", n)]
        good.sort(key=lambda n: (("lite" not in n), [-int(x) for x in re.findall(r"\d+", n)]))     # lite first, newest first
        self.models += [n for n in good if n not in self.models]

    async def ask(self, prompt: str) -> Any:
        """Parsed JSON answer. Raises QAUnavailable when no candidate model works."""
        if not self.key:
            raise QAUnavailable("no GEMINI_API_KEY")
        i = 0
        while i < len(self.models):
            model = self.models[i]
            r = None
            for attempt in range(3):
                try:
                    r = await self.client.post("%s/models/%s:generateContent" % (self.BASE, model),
                                               json={"contents": [{"parts": [{"text": prompt}]}],
                                                     "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
                                               headers={"x-goog-api-key": self.key}, timeout=90)
                except Exception as e:
                    log.warning("Gemini %s call failed: %s", model, type(e).__name__)
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    try:
                        return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                    except Exception:
                        return None      # an answer that is not JSON: the passages of this call get fallback questions
                if r.status_code in (500, 502, 503, 504):
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break                    # 404 (model gone), 429 (quota), 400/403: next model
            if not self._discovered and r is not None and r.status_code in (404, 429):
                await self._discover()
            i += 1
        raise QAUnavailable("no Gemini model could be used")


# ---------------------------------------------------------------- the build
AskFn = Callable[[str], Awaitable[Any]]


async def build_qa_pages(pages: List[Dict[str, Any]], seed_url: str, ask: AskFn, brand: str = "", max_passages: int = MAX_PASSAGES) -> Dict[str, Any]:
    """Split `pages` (the crawl's pages) into question + answer pairs.
    Returns {"qa": [pages for the import: url#cN, title=question, content=answer], "plain": [pages kept as whole pages], "bases": [page urls that got passages], "stats": {...}}.
    A page goes to "plain" when it has no usable text, when the passage limit is reached, or when the AI became unavailable."""
    site = re.sub(r"^www\.", "", urlparse(seed_url).netloc)
    brand = brand or site.split(".")[0].title()
    names = [page_name(p) for p in pages]
    tail = common_suffix(names)
    qa: List[Dict[str, Any]] = []
    plain: List[Dict[str, Any]] = []
    bases: List[str] = []
    stats = {"pages": len(pages), "passages": 0, "ai_questions": 0, "fallback_questions": 0, "ai_unavailable": False, "pages_plain": 0}
    ai_ok = True
    used = 0
    for p, name in zip(pages, names):
        short = name[: -len(tail)].strip() if tail and name.endswith(tail) and len(name) > len(tail) else name
        text = page_text(p.get("content") or "", {name, short})
        chunks = split_chunks(text) if text else []
        if not chunks or not ai_ok or used + len(chunks) > max_passages:
            plain.append(p)
            continue
        questions: Dict[int, str] = {}
        try:
            for start in range(0, len(chunks), BATCH):
                part = chunks[start:start + BATCH]
                listing = "\n\n".join("[id %d]\n%s" % (i, c) for i, c in enumerate(part))
                for attempt in range(2):          # the passages without a usable question are asked once more
                    missing = [i for i in range(len(part)) if (start + i) not in questions]
                    if not missing:
                        break
                    sub = "\n\n".join("[id %d]\n%s" % (i, part[i]) for i in missing)
                    res = await ask(PROMPT.format(site=site, name=short, brand=brand, passages=sub))
                    for e in (res if isinstance(res, list) else []):
                        if isinstance(e, dict) and isinstance(e.get("id"), int) and e["id"] in missing and valid_question(e.get("question")):
                            questions[start + e["id"]] = e["question"].strip()
        except QAUnavailable as e:
            log.warning("AI questions unavailable (%s): the rest of the crawl is imported page by page", e)
            ai_ok = False
            stats["ai_unavailable"] = True
            plain.append(p)
            continue
        used += len(chunks)
        base = p["url"].split("#")[0]
        bases.append(base)
        for n, c in enumerate(chunks, 1):
            q = questions.get(n - 1)
            if q:
                stats["ai_questions"] += 1
            else:
                q = fallback_question(short, c)
                stats["fallback_questions"] += 1
            qa.append({"url": "%s#c%d" % (base, n), "title": q, "content": "%s\n\n%s" % (short, c)})
    stats["passages"] = len(qa)
    stats["pages_plain"] = len(plain)
    return {"qa": qa, "plain": plain, "bases": bases, "stats": stats}


# ---------------------------------------------------------------- the old rows
def rows_to_replace(existing: List[Tuple[int, str]], bases: List[str]) -> List[int]:
    """ids of the rows of the pages in `bases`: the whole-page row (url == base) and passage rows (base#cN)."""
    bs = set(bases)
    return [i for i, u in existing if u.split("#")[0] in bs]


def unchanged_with_passages(pages: List[Dict[str, Any]], existing: List[Tuple[int, str]]) -> List[str]:
    """Urls of pages that did not change since the last crawl and already have passage rows: nothing to do for them."""
    have = {u.split("#")[0] for _, u in existing if "#c" in u}
    return [p["url"].split("#")[0] for p in pages if p.get("change_status") == "skipped_unchanged" and p["url"].split("#")[0] in have]


def domain_title(seed_url: str) -> str:
    return "Crawl: " + re.sub(r"^www\.", "", urlparse(seed_url).netloc)


def read_rows(conn, seed_url: str) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    cur = conn.cursor()
    cur.execute("SELECT id FROM knowledge_knowledgedocument WHERE title = %s AND file_type = 'crawl' ORDER BY id LIMIT 1", (domain_title(seed_url),))
    row = cur.fetchone()
    if not row:
        cur.close()
        return None, []
    cur.execute("SELECT id, source_url FROM knowledge_qapair WHERE document_id = %s", (row[0],))
    rows = [(int(i), u or "") for i, u in cur.fetchall()]
    cur.close()
    return int(row[0]), rows


def backup_and_delete(conn, doc_id: int, ids: List[int], seed_url: str, backup_dir: str = BACKUP_DIR) -> Optional[str]:
    """Saves the rows to a JSON file, then deletes them. Returns the file name (None when there is nothing to delete)."""
    if not ids:
        return None
    cur = conn.cursor()
    cur.execute("SELECT id, question, answer, answer_assamese, source_name, source_url FROM knowledge_qapair WHERE document_id = %s AND id = ANY(%s)", (doc_id, ids))
    rows = [dict(zip(("id", "question", "answer", "answer_assamese", "source_name", "source_url"), r)) for r in cur.fetchall()]
    fn = os.path.join(backup_dir, "qa-replaced-%s-%s.json" % (re.sub(r"[^a-z0-9.]+", "-", domain_title(seed_url)[7:]), time.strftime("%Y%m%d-%H%M%S")))
    with open(fn, "w", encoding="utf-8") as f:
        json.dump({"document_id": doc_id, "rows": rows}, f, ensure_ascii=False)
    cur.execute("DELETE FROM knowledge_qapair WHERE document_id = %s AND id = ANY(%s)", (doc_id, [r["id"] for r in rows]))
    conn.commit()
    cur.close()
    return fn


def qa_enabled(seed_url: str) -> bool:
    if os.getenv("AXOMAI_QA_CHUNKS", "1") == "0":
        return False
    try:
        from crawler.cleaner import load_profiles
        prof = load_profiles().get(re.sub(r"^www\.", "", urlparse(seed_url).netloc.lower()))
        if isinstance(prof, dict) and prof.get("qa_chunks") is False:
            return False
    except Exception:
        pass
    return True


async def push_with_passages(push: Callable[..., Awaitable[Dict[str, Any]]], seed_url: str, job_id: str, pages: List[Dict[str, Any]],
                             get_conn: Callable[[], Any], ask: Optional[AskFn] = None, backup_dir: str = BACKUP_DIR,
                             max_passages: int = MAX_PASSAGES) -> Dict[str, Any]:
    """The whole step: passages + questions, replace the old rows of those pages, import. Falls back to importing the pages as they are.
    `push(pages)` imports a list of pages and returns the import totals."""
    result: Dict[str, Any] = {"mode": "pages"}
    try:
        conn = get_conn()
        try:
            doc_id, existing = read_rows(conn, seed_url)
            skip = set(unchanged_with_passages(pages, existing))
            todo = [p for p in pages if p["url"].split("#")[0] not in skip]
            if not todo:
                return {"mode": "passages", "skipped_unchanged": len(skip), "stats": {}}
            if ask is None:
                gem = Gemini(gemini_key(), client=httpx.AsyncClient())
                try:
                    built = await build_qa_pages(todo, seed_url, gem.ask, max_passages=max_passages)
                finally:
                    await gem.client.aclose()
            else:
                built = await build_qa_pages(todo, seed_url, ask, max_passages=max_passages)
            if not built["qa"]:
                totals = await push(built["plain"] or todo)
                return {"mode": "pages", "totals": totals, "stats": built["stats"]}
            ids = rows_to_replace(existing, built["bases"]) if doc_id else []
            backup = backup_and_delete(conn, doc_id, ids, seed_url, backup_dir) if ids else None
        finally:
            conn.close()
        totals = await push(built["qa"] + built["plain"])
        if totals.get("failed_batches") or totals.get("aborted"):
            log.error("Passage import had failures %s: importing the pages the old way", totals)
            totals = await push(todo)
            return {"mode": "pages_after_failure", "totals": totals, "backup": backup, "stats": built["stats"]}
        return {"mode": "passages", "totals": totals, "replaced_rows": len(ids), "backup": backup, "stats": built["stats"], "skipped_unchanged": len(skip)}
    except Exception as e:      # whatever goes wrong before the rows were touched: import as before
        log.exception("Passage step failed (%s): importing the pages the old way", type(e).__name__)
        totals = await push(pages)
        return {"mode": "pages_after_error", "totals": totals, "error": str(e)}
