# -*- coding: utf-8 -*-
"""Slowly re-fetches the pages of a site that were saved as block pages (CAPTCHA / 429 / Access Denied), puts the real pages
in place of the block pages, runs the site profile's cleaner over the whole set and writes the result to --out.
It changes nothing in the bot or in Axom AI; import the output with chat_import.push_pages afterwards.

    python3 refetch_blocked.py <domain> --glob '/home/admin/axomai-bot/crawled_data/*name*.json' --out /tmp/x.json [--delay 8] [--limit 0]

Stops by itself after 3 block pages in a row (the server's IP is being limited: wait and run again, finished URLs are skipped
through --state).
"""
import argparse
import asyncio
import glob
import json
import os
import random
import re
import sys

import httpx

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
from crawler import cleaner  # noqa: E402
from crawler.extractor import ContentExtractor  # noqa: E402

UA = "Mozilla/5.0 (compatible; AxomAIBot/1.0; +https://aiaxom.co.in)"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=8.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--state", default="")
    a = ap.parse_args()
    prof = cleaner.load_profiles().get(a.domain) or {}
    junk = [re.compile(x) for x in prof.get("junk_urls", [])]
    files = []
    for f in glob.glob(a.glob):
        if os.path.basename(f) in ("schedules.json", "url_catalog.json"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        files.append((d.get("completed_at") or "", d))
    pages = {}
    for _, d in sorted(files, key=lambda x: x[0]):
        for p in d["pages"]:
            pages[p["url"]] = {k: p.get(k) for k in ("url", "title", "content", "word_count", "change_status")}
    state = json.load(open(a.state, encoding="utf-8")) if a.state and os.path.exists(a.state) else {}
    pages.update(state)
    todo = [u for u, p in pages.items() if cleaner.is_block_page(p.get("content") or "") and not any(r.search(u) for r in junk)]
    if a.limit:
        todo = todo[:a.limit]
    print("pages known: %d | block pages to re-fetch: %d | delay %.0fs" % (len(pages), len(todo), a.delay))
    got = bad = streak = 0
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=25) as client:
        for i, u in enumerate(todo, 1):
            try:
                r = await client.get(u)
                r.encoding = r.encoding or "utf-8"
                doc = ContentExtractor.parse_page(r.text, u, r.status_code)
                content = doc["content"] or ""
                blocked = r.status_code in (403, 429) or cleaner.is_block_page(content) or "unusual traffic" in r.text[:3000].lower()
            except Exception as e:
                print("  error", u[-60:], type(e).__name__)
                blocked, doc, content = True, None, ""
            if blocked or not content.strip():
                bad += 1
                streak += 1
                print("  [%d/%d] still blocked: %s" % (i, len(todo), u[-60:]))
                if streak >= 3:
                    print("STOP: 3 block pages in a row - wait and run again")
                    break
            else:
                streak = 0
                got += 1
                pages[u] = {"url": u, "title": doc["title"], "content": content, "word_count": doc["word_count"], "change_status": "updated"}
                if a.state:
                    state[u] = pages[u]
                    json.dump(state, open(a.state, "w", encoding="utf-8"), ensure_ascii=False)
            await asyncio.sleep(a.delay + random.uniform(0, 3))
    print("re-fetched real pages: %d | still blocked: %d" % (got, bad))
    out, s = cleaner.clean_pages(list(pages.values()), prof)
    print("cleaner applied:", s["applied"], s.get("reason", ""), "| pages out:", len(out), "| dropped:", len(s.get("pages_dropped", {})))
    json.dump({"seed_url": "https://%s/" % a.domain, "job_id": "job_refetch_" + a.domain.split(".")[0], "pages": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print("written", a.out)


if __name__ == "__main__":
    asyncio.run(main())
