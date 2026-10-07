# -*- coding: utf-8 -*-
"""For a Blogger blog: gets the full text of posts from the blog's own feed (/feeds/posts/default), which is not rate-limited like
the post pages. By default it only fills in the posts that were saved as block pages; --all takes every post of the blog.
The real pages replace the block pages, the site profile's cleaner runs over the whole set, the result goes to --out.
It changes nothing in the bot or in Axom AI.

    python3 blogger_feed.py <domain> --glob '<crawled_data glob>' --out /tmp/x.json [--all] [--host www.xukhdukh.com]
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import httpx

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
from crawler import cleaner  # noqa: E402
from crawler.extractor import ContentExtractor  # noqa: E402

UA = "Mozilla/5.0 (compatible; AxomAIBot/1.0; +https://aiaxom.co.in)"


def feed_posts(host):
    start, out = 1, []
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60) as c:
        while True:
            r = c.get("https://%s/feeds/posts/default" % host, params={"alt": "json", "max-results": 150, "start-index": start})
            r.raise_for_status()
            feed = r.json()["feed"]
            entries = feed.get("entry") or []
            out += entries
            total = int(feed["openSearch$totalResults"]["$t"])
            start += len(entries)
            print("  feed: %d / %d" % (len(out), total))
            if not entries or start > total:
                return out
            time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    prof = cleaner.load_profiles().get(a.domain) or {}
    junk = [re.compile(x) for x in prof.get("junk_urls", [])]
    pages = {}
    files = []
    for f in glob.glob(a.glob):
        if os.path.basename(f) in ("schedules.json", "url_catalog.json"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        files.append((d.get("completed_at") or "", d))
    for _, d in sorted(files, key=lambda x: x[0]):
        for p in d["pages"]:
            pages[p["url"]] = {k: p.get(k) for k in ("url", "title", "content", "word_count", "change_status")}
    wanted = {u for u, p in pages.items() if cleaner.is_block_page(p.get("content") or "") and not any(r.search(u) for r in junk)}
    print("pages known: %d | block pages: %d" % (len(pages), len(wanted)))
    host = a.host or "www." + a.domain
    filled = added = 0
    for e in feed_posts(host):
        link = next((l["href"] for l in e.get("link", []) if l.get("rel") == "alternate"), "")
        link = re.sub(r"^http://", "https://", link.split("?")[0])
        if not link or (not a.all and link not in wanted):
            continue
        html = (e.get("content") or e.get("summary") or {}).get("$t", "")
        text = ContentExtractor.extract_content("<html><body><article>%s</article></body></html>" % html, url=link) or ""
        title = (e.get("title") or {}).get("$t", "").strip()
        if len(text.split()) < 8:
            continue
        content = (title + "\n\n" + text) if title and not text.startswith(title) else text
        if link in pages:
            filled += 1
        else:
            added += 1
        pages[link] = {"url": link, "title": title or link, "content": content, "word_count": len(re.findall(r"\w+", content)), "change_status": "updated"}
    print("block pages replaced by feed text: %d | new posts added: %d | block pages still left: %d" % (
        filled, added, sum(cleaner.is_block_page(p.get("content") or "") for p in pages.values())))
    out, s = cleaner.clean_pages(list(pages.values()), prof)
    print("cleaner applied:", s["applied"], s.get("reason", ""), "| pages out:", len(out), "| dropped:", len(s.get("pages_dropped", {})))
    json.dump({"seed_url": "https://%s/" % a.domain, "job_id": "job_feed_" + a.domain.split(".")[0], "pages": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print("written", a.out)


if __name__ == "__main__":
    main()
