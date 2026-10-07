#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dry-run report for cleaning a crawl of the Axom AI crawler bot. It NEVER changes any file in crawled_data.

    python3 clean_report.py <job.json> [--out reports] [--threshold 0.30] [--min-words 100]
                                      [--junk REGEX ...] [--keep-pages REGEX ...] [--terms terms.txt]

It answers: which pages are duplicates or junk URLs, which lines repeat on many pages (menus, cards, widgets), what would be
removed from every page, and - most important - whether any real content would be lost.

Safety rules the cleaning follows (the report checks all of them):
  1. A line is removed only if it appears on at least `threshold` of the pages (and on at least 3). A line that is on one page
     only is never removed.
  2. Protected lines are never removed: a page's own title, the first lines at the top of a page (its heading and duration),
     "Day 1:" style labels, long paragraphs (40+ words), and lines with a price, phone number or e-mail address.
  3. Pages are only dropped when they are an exact copy of an earlier page or their URL matches a junk pattern; the report
     lists every line that exists ONLY on a dropped page, so nothing disappears unseen.
  4. Pages with few words, or that lose a lot of text, are flagged for a human, not changed.
  5. Nothing vanishes from the whole crawl: a line that would be removed from every page it is on is kept ONCE, in a single
     "site-wide content" page, and pages matching --keep-pages (listing / hub pages where the repeated lines ARE the content)
     are never cleaned.
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from collections import Counter

DEFAULT_JUNK = [r"/new-demo/", r"[?&]e-page-", r"[?&](page|paged|p)=\d+", r"/tag/", r"/page/\d+", r"[?&](replytocom|share|print)="]
DAY_LABEL = re.compile(r"^(day|दिन|দিন)\s*\d+\s*[:.\-–]?\s*$", re.I)
TOP_LINES = 6  # lines at the top of a page are its heading, duration, intro: protected when they have 3+ words
PROTECT = re.compile(r"(₹|\bRs\.?\s?\d|\bINR\b|\$\s?\d|€\s?\d|@\w+\.\w+|\+\d{2}[\s\d-]{8,}|\b\d{10}\b)")


def words(s):
    return len(re.findall(r"\w+", s, re.UNICODE))


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    pages = []
    for p in d.get("pages", []):
        lines = [norm(x) for x in (p.get("content") or "").split("\n")]
        lines = [x for x in lines if x]
        pages.append({"url": p.get("url", ""), "title": norm(p.get("title", "")), "lines": lines})
    return d, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--threshold", type=float, default=0.30)
    ap.add_argument("--min-words", type=int, default=100)
    ap.add_argument("--junk", nargs="*", default=DEFAULT_JUNK)
    ap.add_argument("--keep-pages", nargs="*", default=[], help="regexes of pages that are never cleaned (listing / hub pages)")
    ap.add_argument("--terms", default="", help="text file, one must-keep phrase per line")
    a = ap.parse_args()

    job, pages = load(a.job)
    junk_re = [re.compile(x) for x in a.junk]
    keep_re = [re.compile(x) for x in a.keep_pages]
    N = len(pages)

    # ---- which pages are dropped
    seen, dropped = {}, {}
    for p in pages:
        h = hashlib.md5(norm(" ".join(p["lines"])).encode()).hexdigest()
        reason = None
        if any(r.search(p["url"]) for r in junk_re):
            reason = "junk URL pattern"
        elif h in seen:
            reason = "exact copy of " + seen[h]
        if reason:
            dropped[p["url"]] = reason
        else:
            seen[h] = p["url"]
    kept_pages = [p for p in pages if p["url"] not in dropped]

    # ---- line frequency over the kept pages
    df = Counter()
    for p in kept_pages:
        for l in set(p["lines"]):
            df[l] += 1
    need = max(3, math.ceil(a.threshold * len(kept_pages)))
    boiler = {l for l, n in df.items() if n >= need}

    def protected(line, page):
        if line == page["title"] or words(line) >= 40 or PROTECT.search(line) or DAY_LABEL.match(line):
            return True
        top = page["lines"][:TOP_LINES]
        return line in top and words(line) >= 3

    # ---- per page result
    rows = []
    for p in kept_pages:
        hub = any(r.search(p["url"]) for r in keep_re)
        removed = [] if hub else [l for l in p["lines"] if l in boiler and not protected(l, p)]
        keep = p["lines"] if hub else [l for l in p["lines"] if not (l in boiler and not protected(l, p))]
        wb, wa = words(" ".join(p["lines"])), words(" ".join(keep))
        rows.append({"url": p["url"], "before": wb, "after": wa, "removed_lines": len(removed),
                     "retention": (wa / wb) if wb else 1.0, "removed": removed, "keep": keep, "title": p["title"], "hub": hub})

    # ---- safety checks
    all_df = Counter()
    for p in pages:
        for l in set(p["lines"]):
            all_df[l] += 1
    only_on_dropped = []
    kept_lines = set(l for p in kept_pages for l in p["lines"])
    for p in pages:
        if p["url"] in dropped:
            lost = [l for l in set(p["lines"]) if l not in kept_lines]
            if lost:
                only_on_dropped.append((p["url"], dropped[p["url"]], lost))
    unique_removed = [l for r in rows for l in r["removed"] if df[l] == 1]  # must be empty by rule 1
    present = set(l for r in rows for l in r["keep"])
    vanish, seen_v = [], set()
    for r in rows:
        for l in r["removed"]:
            if l not in present and l not in seen_v:
                seen_v.add(l)
                vanish.append(l)  # first-appearance order: removed from every page it was on
    terms = [t.strip() for t in open(a.terms, encoding="utf-8")] if a.terms and os.path.exists(a.terms) else []
    terms = [t for t in terms if t]
    before_text = "\n".join("\n".join(p["lines"]) for p in pages).lower()
    after_text = ("\n".join("\n".join(r["keep"]) for r in rows) + "\n" + "\n".join(vanish)).lower()
    term_check = [(t, t.lower() in before_text, t.lower() in after_text) for t in terms]

    tot_before = sum(words(" ".join(p["lines"])) for p in pages)
    sitewide_words = words(" ".join(vanish))
    tot_after = sum(r["after"] for r in rows) + sitewide_words
    flags = [r for r in rows if r["after"] < a.min_words or r["retention"] < 0.60]

    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, os.path.basename(a.job).replace(".json", ""))
    L = []
    w = L.append
    w("# Cleaning report (dry run, nothing was changed)\n")
    w("Job: `%s`  |  seed: %s  |  pages in file: %d\n" % (job.get("job_id"), job.get("seed_url"), N))
    w("## Summary\n")
    w("| | |\n|---|---|")
    w("| Words now | %d |" % tot_before)
    w("| Words after cleaning | %d (%.0f%% kept) |" % (tot_after, 100.0 * tot_after / max(1, tot_before)))
    w("| Pages dropped (exact copy or junk URL) | %d |" % len(dropped))
    w("| Pages never cleaned (listing / hub pages) | %d |" % sum(1 for r in rows if r["hub"]))
    w("| Site-wide content page (repeated lines, kept once) | %d lines, %d words |" % (len(vanish), sitewide_words))
    w("| Pages kept | %d |" % len(kept_pages))
    w("| Lines treated as site-wide (on >= %d of %d pages) | %d |" % (need, len(kept_pages), len(boiler)))
    w("| Pages flagged for a human look | %d |" % len(flags))
    w("")
    w("## Safety checks\n")
    w("- Lines that are on ONE page only and would still be removed: **%d** (must be 0)" % len(unique_removed))
    w("- Dropped pages that hold lines found nowhere else: **%d page(s)**" % len(only_on_dropped))
    w("- Lines that would vanish from the whole crawl (removed from every page): **%d** -> all of them are kept once in the site-wide page, so lost: **0**" % len(vanish))
    if terms:
        miss = [t for t, b, af in term_check if b and not af]
        w("- Must-keep phrases: %d checked, **%d lost** %s" % (len(terms), len(miss), ("-> " + ", ".join(miss)) if miss else ""))
        absent = [t for t, b, af in term_check if not b]
        if absent:
            w("  (not in the crawl at all: %s)" % ", ".join(absent))
    w("")
    w("## Dropped pages\n")
    w("| Page | Why | Lines found only here |\n|---|---|---|")
    lost_map = {u: len(l) for u, _, l in only_on_dropped}
    for u, r in dropped.items():
        w("| %s | %s | %d |" % (u, r, lost_map.get(u, 0)))
    w("")
    if only_on_dropped:
        w("### Lines that exist only on a dropped page (look at these before approving)\n")
        for u, r, lost in only_on_dropped:
            w("**%s** (%s)" % (u, r))
            for l in lost[:12]:
                w("- %s" % l[:140])
            w("")
    w("## Flagged pages (few words, or lose more than 40%)\n")
    w("| Page | Words before | After | Kept |\n|---|---|---|---|")
    for r in sorted(flags, key=lambda x: x["retention"]):
        w("| %s | %d | %d | %.0f%% |" % (r["url"], r["before"], r["after"], 100 * r["retention"]))
    w("")
    w("## Lines treated as site-wide (top 40, with the number of pages)\n")
    w("| Pages | Line | Removed? |\n|---|---|---|")
    for l, n in df.most_common(60):
        if l in boiler:
            prot = any(protected(l, p) for p in kept_pages if l in p["lines"])
            w("| %d | %s | %s |" % (n, l[:100].replace("|", "/"), "kept (protected)" if prot else "yes"))
    w("")
    w("## Site-wide content page (these lines were repeated on many pages; they are kept once here)\n")
    for l in vanish[:80]:
        w("- %s" % l[:140])
    if len(vanish) > 80:
        w("- ... and %d more" % (len(vanish) - 80))
    w("")
    w("## Every page: before and after\n")
    w("| Page | Before | After | Kept | Lines removed |\n|---|---|---|---|---|")
    for r in rows:
        w("| %s%s | %d | %d | %.0f%% | %d |" % (r["url"], "  (never cleaned)" if r["hub"] else "", r["before"], r["after"], 100 * r["retention"], r["removed_lines"]))
    open(base + "_report.md", "w", encoding="utf-8").write("\n".join(L))

    # ---- a few pages, before and after, to read with your own eyes
    random.seed(7)
    pick = sorted(rows, key=lambda x: x["retention"])[:2] + random.sample(rows, min(3, len(rows)))
    S = ["# Before / after samples (read these)\n"]
    for r in pick:
        S.append("## %s\nwords %d -> %d\n\n### REMOVED lines (%d)\n" % (r["url"], r["before"], r["after"], r["removed_lines"]))
        S.append("\n".join("- " + l[:140] for l in r["removed"][:25]) or "(none)")
        S.append("\n### KEPT text (first 900 characters)\n\n```\n%s\n```\n" % "\n".join(r["keep"])[:900])
    open(base + "_samples.md", "w", encoding="utf-8").write("\n".join(S))

    print("report :", base + "_report.md")
    print("samples:", base + "_samples.md")
    print("words %d -> %d incl. site-wide page (%.0f%% kept) | dropped pages %d | flagged %d | unique lines removed %d | "
          "pages with lines only on dropped pages %d | lines kept once in the site-wide page %d"
          % (tot_before, tot_after, 100.0 * tot_after / max(1, tot_before), len(dropped), len(flags), len(unique_removed), len(only_on_dropped), len(vanish)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
