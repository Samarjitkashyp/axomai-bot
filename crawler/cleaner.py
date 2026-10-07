# -*- coding: utf-8 -*-
"""Cleans the pages of a finished crawl for the sites that have a profile in site_profiles.json (opt-in, per domain).

Called from StorageManager.save_crawl_job. The crawl as it came from the web is always archived first (crawled_data_raw/),
so nothing is ever lost, and any problem here falls back to the untouched pages.

What it does (the same rules as tools/clean_report.py, which shows them on a finished crawl without changing anything):
  * drops pages that are an exact copy of an earlier page or whose URL matches a junk pattern of the profile;
  * removes lines that repeat on many pages (menus, cards, widgets), but only when the line is on at least `threshold`
    of the pages and on at least 3; a line that is on one page only is never removed;
  * never removes: the page title, the first lines of a page (profile `top_lines`, 6 by default), "Day 1:" labels, long paragraphs, lines with a price,
    phone number or e-mail address;
  * drops block pages (a firewall's or Google's "unusual traffic / Access Denied" message saved instead of the page);
  * drops a line that is repeated word for word later on the same page (4+ words; the first one stays);
  * never cleans the pages that match `keep_pages` (listing / hub pages where the repeated lines ARE the content).
Safety gates: it gives up and returns the pages untouched if a must-keep phrase of the profile would be lost, if more than
60% of the words would go, or if anything raises.
"""
import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("axomai.cleaner")

PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_profiles.json")
MIN_PAGES_FOR_BOILERPLATE = 10   # with fewer pages the "repeats on many pages" statistics mean nothing
MIN_KEPT_WORDS_SHARE = 0.40      # gate: keep at least this share of the words
TOP_LINES = 6                    # the first lines of a page (heading, duration, intro) are protected when they have 3+ words
DAY_LABEL = re.compile(r"^(day|दिन|দিন)\s*\d+\s*[:.\-–]?\s*$", re.I)
PROTECT = re.compile(r"(₹|\bRs\.?\s?\d|\bINR\b|\$\s?\d|€\s?\d|@\w+\.\w+|\+\d{2}[\s\d-]{8,}|\b\d{10}\b)")


# Pages that are not the page but a message from a firewall or a search engine ("unusual traffic", "Access Denied", a Cloudflare
# challenge). A blocked crawl saves these as if they were content. Strong markers only, and only on short pages.
GOOGLE_BLOCK = ("unusual traffic", "really you sending", "our systems have detected")
OTHER_BLOCK = ("access denied", "403 forbidden", "429 too many requests", "just a moment", "attention required", "signaturedoesnotmatch",
               "email protection | cloudflare", "error 1020", "you have been blocked", "verify you are human")


def is_block_page(content: str) -> bool:
    text = (content or "")[:700].lower()
    n = words(content or "")
    if n < 400 and any(m in text for m in GOOGLE_BLOCK):
        return True
    return n < 120 and any(m in text for m in OTHER_BLOCK)


def words(s: str) -> int:
    return len(re.findall(r"\w+", s, re.UNICODE))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def load_profiles() -> Dict[str, Any]:
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # a broken profile file must never break a crawl
        log.warning("site_profiles.json could not be read: %s", e)
        return {}


def profile_for(seed_url: str) -> Optional[Dict[str, Any]]:
    domain = urlparse(seed_url).netloc.lower().replace("www.", "")
    prof = load_profiles().get(domain)
    return prof if isinstance(prof, dict) and prof.get("enabled") else None


def clean_pages(pages: List[Dict[str, Any]], prof: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    junk_re = [re.compile(x) for x in prof.get("junk_urls", [])]
    keep_re = [re.compile(x) for x in prof.get("keep_pages", [])]
    threshold = float(prof.get("threshold", 0.30))
    terms = [t for t in prof.get("terms", []) if t]
    top_n = int(prof.get("top_lines", TOP_LINES))  # 0 for news sites, where the top of a page is the menu and the trending ticker

    lines_of = {}
    for p in pages:
        lines_of[p["url"]] = [x for x in (_norm(l) for l in (p.get("content") or "").split("\n")) if x]
    words_before = sum(words(" ".join(v)) for v in lines_of.values())

    # 1) which pages are dropped
    seen, dropped = {}, {}
    for p in pages:
        lines = lines_of[p["url"]]
        h = hashlib.md5(_norm(" ".join(lines)).encode("utf-8")).hexdigest()
        if any(r.search(p["url"]) for r in junk_re):
            dropped[p["url"]] = "junk URL"
        elif prof.get("drop_block_pages", True) and is_block_page(p.get("content") or ""):
            dropped[p["url"]] = "block page (not the real page)"
        elif lines and h in seen:
            dropped[p["url"]] = "exact copy of " + seen[h]
        else:
            seen[h] = p["url"]
    kept = [p for p in pages if p["url"] not in dropped]

    # 2) lines that repeat on many pages
    boiler = set()
    if len(kept) >= MIN_PAGES_FOR_BOILERPLATE:
        df = Counter()
        for p in kept:
            for l in set(lines_of[p["url"]]):
                df[l] += 1
        need = max(3, math.ceil(threshold * len(kept)))
        boiler = {l for l, n in df.items() if n >= need}

    def protected(line: str, p: Dict[str, Any]) -> bool:
        title = _norm(p.get("title", ""))
        if line == title or words(line) >= 40 or PROTECT.search(line) or DAY_LABEL.match(line):
            return True
        return top_n > 0 and line in lines_of[p["url"]][:top_n] and words(line) >= 3

    words_before_kept = sum(words(" ".join(lines_of[p["url"]])) for p in kept)  # the safety gate looks at the pages that stay, not at junk pages
    out, removed_lines = [], []
    for p in kept:
        lines = lines_of[p["url"]]
        hub = any(r.search(p["url"]) for r in keep_re)
        keep = lines if hub else [l for l in lines if not (l in boiler and not protected(l, p))]
        if not hub:  # the same line twice on one page (title as meta and as heading, a lead paragraph twice): keep the first one
            seen_here, deduped = set(), []
            for l in keep:
                if words(l) >= 4 and l in seen_here:
                    continue
                seen_here.add(l)
                deduped.append(l)
            keep = deduped
        if not hub:
            removed_lines.extend(l for l in lines if l in boiler and not protected(l, p))
        q = dict(p)
        q["content"] = "\n\n".join(keep)
        q["word_count"] = words(q["content"])
        out.append(q)

    # lines removed from every page they were on: kept once in the summary (and in the raw archive)
    present = set(l for p in out for l in p["content"].split("\n\n"))
    sitewide, seen_l = [], set()
    for l in removed_lines:
        if l not in present and l not in seen_l:
            seen_l.add(l)
            sitewide.append(l)

    words_after = sum(p["word_count"] for p in out)
    summary = {
        "applied": True, "pages_in": len(pages), "pages_out": len(out), "pages_dropped": dropped,
        "boilerplate_lines": len(boiler), "words_before": words_before, "words_after": words_after,
        "kept_share": round(words_after / words_before, 3) if words_before else 1.0,
        "kept_pages_share": round(words_after / words_before_kept, 3) if words_before_kept else 1.0,
        "sitewide_lines": sitewide[:200],
    }

    # 3) safety gates: any doubt -> the untouched pages
    before_text = "\n".join("\n".join(v) for v in lines_of.values()).lower()
    after_text = "\n".join(p["content"] for p in out).lower()
    lost = [t for t in terms if t.lower() in before_text and t.lower() not in after_text]
    if lost:
        return pages, {"applied": False, "reason": "must-keep phrases would be lost: " + ", ".join(lost[:10])}
    if words_before_kept and words_after < MIN_KEPT_WORDS_SHARE * words_before_kept:
        return pages, {"applied": False, "reason": "would remove more than %d%% of the words" % round(100 * (1 - MIN_KEPT_WORDS_SHARE))}
    return out, summary


def apply_profile(seed_url: str, pages: List[Dict[str, Any]]) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """Returns None when the site has no (enabled) profile; otherwise (pages, summary). Never raises."""
    prof = profile_for(seed_url)
    if not prof:
        return None
    try:
        return clean_pages(pages, prof)
    except Exception as e:
        log.exception("cleaning failed for %s, using the pages as crawled", seed_url)
        return pages, {"applied": False, "reason": "error: %s" % e}
