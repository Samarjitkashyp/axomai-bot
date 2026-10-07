# -*- coding: utf-8 -*-
"""Runs one crawl with the bot's own engine, from the command line (same as the dashboard's Start Crawl, without the login).
It saves the crawl like the dashboard does (crawled_data/, cleaned if the site has a profile) and registers the site for the
auto-crawl schedule. It does NOT import into Axom AI: do that after checking the pages (chat_import.push_pages).

    python3 run_crawl.py https://example.com/ [--max-pages 100] [--depth 3] [--delay 2] [--no-js] [--subdomains] [--force]
"""
import argparse
import asyncio
import os
import sys
import uuid

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
os.chdir(BOT)
from crawler.engine import CrawlerEngine, CrawlerJob  # noqa: E402
from crawler.storage import StorageManager  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--no-js", action="store_true")
    ap.add_argument("--subdomains", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    job = CrawlerJob(job_id="job_%s" % uuid.uuid4().hex[:8], seed_url=a.url, max_pages=a.max_pages, max_depth=a.depth, crawl_delay=a.delay,
                     render_js=not a.no_js, force_recrawl=a.force, include_subdomains=a.subdomains)
    engine = CrawlerEngine(job=job, storage_manager=StorageManager())
    await engine.run()
    print("job", job.job_id, "status", job.status, "pages", job.pages_crawled)
    if job.status == "completed":
        from app.scheduler import register_crawl
        register_crawl(a.url)
        print("registered for the auto-crawl schedule")


if __name__ == "__main__":
    asyncio.run(main())
