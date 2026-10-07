# -*- coding: utf-8 -*-
"""Dry run of a CANDIDATE site profile with the bot's real cleaner, on every saved crawl of a site (one version per URL).
It changes nothing in the bot: the candidate profile is read from a separate JSON file and the cleaned pages go to /tmp only.

    python3 site_dryrun.py <domain> <profile.json> [--glob 'crawled_data/*name*.json'] [--out /tmp/<domain>_cleaned.json] [--top 25]

<profile.json> holds the profile (the same keys as an entry of crawler/site_profiles.json), either directly or under its domain.
"""
import argparse
import collections
import glob
import json
import os
import sys
from urllib.parse import urlparse

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
from crawler import cleaner  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("profile")
    ap.add_argument("--glob", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    prof = json.load(open(a.profile, encoding="utf-8"))
    prof = prof.get(a.domain, prof)
    pattern = a.glob or BOT + "/crawled_data/*%s*.json" % a.domain.split(".")[0]
    files = []
    for f in glob.glob(pattern):
        if os.path.basename(f) in ("schedules.json", "url_catalog.json"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        files.append((d.get("completed_at") or "", d))
    pages = {}
    for _, d in sorted(files, key=lambda x: x[0]):
        for p in d["pages"]:
            pages[p["url"]] = {k: p.get(k) for k in ("url", "title", "content", "word_count", "change_status")}
    pages = list(pages.values())
    orig = {p["url"]: p for p in pages}
    out, s = cleaner.clean_pages(pages, prof)
    print("input: %d pages from %d crawl file(s)" % (len(pages), len(files)))
    print("applied:", s["applied"], s.get("reason", ""))
    if not s["applied"]:
        return
    stay = [p for p in pages if p["url"] not in s["pages_dropped"]]
    wb = sum(len((p["content"] or "").split()) for p in stay)
    wa = sum(len(p["content"].split()) for p in out)
    why = collections.Counter(v.split(" of ")[0] if v.startswith("exact") else v for v in s["pages_dropped"].values())
    print("pages out: %d | dropped: %d %s" % (s["pages_out"], len(s["pages_dropped"]), dict(why)))
    print("words of the pages that stay: %d -> %d (%.0f%% kept)" % (wb, wa, 100.0 * wa / max(1, wb)))
    print("hosts left:", dict(collections.Counter(urlparse(p["url"]).netloc for p in out).most_common(4)))
    # what is removed most often
    removed = collections.Counter()
    for p in out:
        before = {l.strip() for l in (orig[p["url"]]["content"] or "").split("\n") if l.strip()}
        after = set(p["content"].split("\n\n"))
        for l in before - after:
            removed[l] += 1
    print("\nlines removed most often (pages, line):")
    for l, n in removed.most_common(a.top):
        print("  %5d  %s" % (n, l[:90]))
    ratio = lambda p: len(p["content"].split()) / max(1, len((orig[p["url"]]["content"] or "").split()))
    print("\nthe 4 pages that lose the most:")
    for p in sorted(out, key=ratio)[:4]:
        print("  %s  %d -> %d words" % (p["url"][-62:], len(orig[p["url"]]["content"].split()), len(p["content"].split())))
    mid = sorted(out, key=ratio)[len(out) // 2]
    print("\ntypical page:", mid["url"][-70:], "| words %d -> %d" % (len(orig[mid["url"]]["content"].split()), len(mid["content"].split())))
    print("BEFORE:", (orig[mid["url"]]["content"] or "")[:260].replace("\n", " | "))
    print("AFTER :", mid["content"][:260].replace("\n", " | "))
    dest = a.out or "/tmp/%s_cleaned.json" % a.domain
    json.dump({"seed_url": "https://%s/" % a.domain, "job_id": "job_cleanup_" + a.domain.split(".")[0], "pages": out}, open(dest, "w", encoding="utf-8"), ensure_ascii=False)
    print("\ncleaned pages written to", dest)


if __name__ == "__main__":
    main()
