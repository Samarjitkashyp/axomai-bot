# -*- coding: utf-8 -*-
"""Sends the pages of a crawl to Axom AI (/api/import-crawl/) in small batches, with retries.

Before this, a whole crawl went in ONE request: a crawl of a thousand pages or more could time out and then nothing arrived
(the import of the 5,000-page news18 crawl failed this way). Now a failure costs one batch of 200 pages at most, and it is retried.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("axomai.import")

BATCH_SIZE = 200
RETRIES = 3
BACKOFF_SECONDS = (2, 6, 12)
FATAL_STATUS = (401, 403)  # wrong token: retrying or sending the other batches is pointless


async def push_pages(
    url: str,
    token: str,
    seed_url: str,
    job_id: str,
    pages: List[Dict[str, Any]],
    batch_size: int = BATCH_SIZE,
    retries: int = RETRIES,
    timeout: float = 300,
    backoff: tuple = BACKOFF_SECONDS,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    items = [
        {"url": p.get("url", ""), "title": p.get("title", ""), "content": p.get("content", "")}
        for p in pages
        if (p.get("content") or "").strip()
    ]
    totals = {"pages": len(items), "batches": 0, "failed_batches": 0, "created": 0, "updated": 0, "embedded": 0, "aborted": False}
    if not items:
        return totals
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        for start in range(0, len(items), batch_size):
            part = items[start:start + batch_size]
            n = start // batch_size + 1
            total_batches = (len(items) + batch_size - 1) // batch_size
            ok = False
            for attempt in range(1, retries + 1):
                try:
                    resp = await client.post(url, json={"seed_url": seed_url, "job_id": job_id, "pages": part}, headers={"X-Bot-Token": token})
                    if resp.status_code == 200:
                        res = resp.json()
                        for k in ("created", "updated", "embedded"):
                            totals[k] += int(res.get(k) or 0)
                        ok = True
                        break
                    log.error("Chat import %s batch %d/%d failed (%d, try %d/%d): %s", seed_url, n, total_batches, resp.status_code, attempt, retries, resp.text[:200])
                    if resp.status_code in FATAL_STATUS:
                        totals["aborted"] = True
                        break
                except Exception as e:  # timeouts have an empty message: say what it was
                    log.error("Chat import %s batch %d/%d error (try %d/%d): %s %s", seed_url, n, total_batches, attempt, retries, type(e).__name__, e)
                if attempt < retries:
                    await asyncio.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
            totals["batches"] += 1
            if not ok:
                totals["failed_batches"] += 1
            if totals["aborted"]:
                break
    finally:
        if own:
            await client.aclose()
    log.info("Chat import %s: %d pages in %d batch(es) - created=%d updated=%d embedded=%d failed_batches=%d",
             seed_url, totals["pages"], totals["batches"], totals["created"], totals["updated"], totals["embedded"], totals["failed_batches"])
    return totals
