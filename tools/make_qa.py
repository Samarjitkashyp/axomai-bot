# -*- coding: utf-8 -*-
"""Preview of what the bot's passage step (app/qa_chunks.py) makes of a crawl: passages + one AI question each, written to a file.
The bot does the same automatically after every crawl; this tool is for looking at a sample first. It changes nothing in Axom AI.

    python3 make_qa.py --crawl crawled_data/job_x_site.json --out /tmp/qa.json [--env-file /home/admin/axom_ai/.env]
                       [--limit-pages 0] [--brand "Digihive Assam"] [--model gemini-3.5-flash-lite] [--show 12]

The API key is read from GEMINI_API_KEY (or from --env-file, or from the file the bot uses); it is never printed.
"""
import argparse
import asyncio
import json
import os
import sys

import httpx

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
from app import qa_chunks  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env-file", default="")
    ap.add_argument("--model", default=qa_chunks.DEFAULT_MODEL)
    ap.add_argument("--brand", default="")
    ap.add_argument("--limit-pages", type=int, default=0)
    ap.add_argument("--show", type=int, default=12, help="how many pairs to print as a sample")
    a = ap.parse_args()
    if a.env_file:
        from dotenv import load_dotenv
        load_dotenv(a.env_file)
    crawl = json.load(open(a.crawl, encoding="utf-8"))
    pages = crawl["pages"][: a.limit_pages or None]
    async with httpx.AsyncClient() as client:
        gem = qa_chunks.Gemini(qa_chunks.gemini_key(), a.model, client)
        built = await qa_chunks.build_qa_pages(pages, crawl.get("seed_url", ""), gem.ask, brand=a.brand)
    qa = built["qa"]
    json.dump({"seed_url": crawl.get("seed_url", ""), "job_id": "job_qa_preview", "pages": qa}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("stats:", built["stats"], "| written:", a.out)
    step = max(1, len(qa) // max(1, a.show))
    for it in qa[::step][: a.show]:
        print("\nQ: %s\nA: (%d words) %s" % (it["title"], len(it["content"].split()), it["content"][:170].replace("\n", " | ")))


if __name__ == "__main__":
    asyncio.run(main())
