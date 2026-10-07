# -*- coding: utf-8 -*-
"""Clean content sources for sites that publish their content through an API, so the page text comes without menus, footers
and buttons. Used by the crawler engine before it crawls links; anything unexpected returns [] and the normal crawl runs.

WordPress: /wp-json/wp/v2/<type> gives the title and the article HTML of every page, post and custom post type.
"""
import asyncio
import html as htmllib
import re
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import httpx

from crawler.extractor import ContentExtractor

SKIP_TYPES = {"attachment", "nav_menu_item", "wp_block", "wp_template", "wp_template_part", "wp_navigation", "wp_global_styles",
              "wp_font_family", "wp_font_face", "revision", "customize_changeset", "oembed_cache", "user_request", "custom_css"}
# page builders, form plugins and shop internals register post types too; their entries are templates, not content
SKIP_TYPE_PREFIXES = ("elementor", "e-floating", "e-landing", "acf-", "rank_math", "wpcf7", "shop_", "product_variation", "scheduled-action", "jet-",
                      "fusion_", "et_", "wpforms", "ct_template", "oxy_", "tablepress", "mc4wp", "amp_", "wp_", "nav_menu", "wc_", "yith", "elementskit")
# utility pages of a theme (account, login, shop, listings of people), not content
JUNK_SLUGS = re.compile(r"^(users?|members?|login|log-in|logout|register|signup|sign-up|reset|reset-password|lost-password|forgot-password|my-account|account|"
                        r"profile|dashboard|cart|checkout|wishlist|compare|search|sitemap|404|home|home-\d+|homepage|homepage-\d+|author|authors|sample-page(-\d+)?|page-without-ads|bs-[\w-]+)$", re.I)
PER_PAGE = 100
MIN_WORDS = 8          # a page with less text than this is not worth a row
MIN_REAL_PAGES = 3     # fewer than this many pages with real text: the API is not the site's content, crawl normally
API_DELAY = 1.0


def _words(s: str) -> int:
    return len(re.findall(r"\w+", s or "", re.UNICODE))


def _plain(fragment: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def article_text(title: str, content_html: str, url: str) -> str:
    text = ContentExtractor.extract_content("<html><body><article>%s</article></body></html>" % content_html, url=url) or ""
    text = text.strip()
    if title and not text.startswith(title):
        text = title + "\n\n" + text
    return text


async def wordpress_pages(client: httpx.AsyncClient, seed_url: str, max_pages: int, log: Callable[[str], None] = lambda m: None) -> List[Dict[str, Any]]:
    """Pages (posts first newest, after the pages) of a WordPress site, in the crawler's page format. [] if it is not usable."""
    try:
        u = urlparse(seed_url)
        if u.path not in ("", "/"):   # a crawl of one section of a site is a normal crawl
            return []
        base = "%s://%s" % (u.scheme, u.netloc)
        r = await client.get(base + "/wp-json/wp/v2/types", follow_redirects=True, timeout=20)
        if r.status_code != 200 or not isinstance(r.json(), dict):
            return []
        final = urlparse(str(r.url))
        base = "%s://%s" % (final.scheme, final.netloc)
        types = [(slug, t.get("rest_base")) for slug, t in r.json().items()
                 if slug not in SKIP_TYPES and not slug.startswith(SKIP_TYPE_PREFIXES) and t.get("rest_base")]
        types.sort(key=lambda x: (x[0] != "page", x[0] == "post", x[0]))   # pages, custom types, then posts
        out: List[Dict[str, Any]] = []
        for slug, rest_base in types:
            page = 1
            taken = 0
            limit = max_pages if slug == "post" else max(1, max_pages // (3 if slug == "page" else 2))   # a news site's posts must not be crowded out by its pages
            while len(out) < max_pages and taken < limit:
                resp = await client.get(
                    "%s/wp-json/wp/v2/%s" % (base, rest_base),
                    params={"per_page": PER_PAGE, "page": page, "orderby": "date", "order": "desc", "_fields": "link,title,content,type"},
                    follow_redirects=True, timeout=40)
                if resp.status_code != 200:
                    break
                items = resp.json()
                if not isinstance(items, list) or not items:
                    break
                for it in items:
                    link = (it.get("link") or "").split("#")[0]
                    content = it.get("content") or {}
                    if not link or content.get("protected"):
                        continue
                    if JUNK_SLUGS.match(urlparse(link).path.strip("/").split("/")[-1] or "home"):
                        continue
                    title = _plain((it.get("title") or {}).get("rendered", ""))
                    text = article_text(title, content.get("rendered", ""), link)
                    if _words(text) < (30 if slug == "page" else MIN_WORDS):
                        continue
                    out.append({"url": link, "status_code": 200, "title": title or link, "description": "", "canonical": link, "content": text,
                                "word_count": _words(text), "links_found_count": 0, "discovered_links": [], "documents_found_count": 0,
                                "document_links": [], "depth": 0})
                    taken += 1
                    if len(out) >= max_pages or taken >= limit:
                        break
                if len(items) < PER_PAGE:
                    break
                page += 1
                await asyncio.sleep(API_DELAY)
            log("WordPress API: %s -> %d pages so far" % (slug, len(out)))
            if len(out) >= max_pages:
                break
        real = sum(1 for p in out if p["word_count"] >= 30)
        return out if real >= MIN_REAL_PAGES else []
    except Exception as e:
        log("WordPress API not used: %s %s" % (type(e).__name__, e))
        return []
