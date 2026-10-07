# -*- coding: utf-8 -*-
"""Content-only view of a crawl for the Excel (CSV), PDF and JSON exports: for every page its name and its text, nothing else
(no URL, no links, no buttons, no images). The same list feeds all three exports so they always agree."""
import re
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

from crawler.cleaner import is_ui_line


def page_name(page: Dict[str, Any]) -> str:
    """The page's title; when the title is just the URL (or empty), a name made from the last part of the URL."""
    title = (page.get("title") or "").strip()
    url = (page.get("url") or "").strip()
    if title and title != url and not re.match(r"^https?://", title, re.I):
        return title
    path = unquote(urlparse(url).path).strip("/")
    if not path:
        return "Home"
    slug = re.sub(r"\.(html?|php|aspx?)$", "", path.split("/")[-1], flags=re.I)
    return re.sub(r"[-_]+", " ", slug).strip().title() or "Untitled"


SEPARATORS = (" | ", " - ", " – ", " — ", " :: ", " « ", " » ")


def common_suffix(names: List[str]) -> str:
    """The site name that most page titles end with ("Home | My Voyage", "About Us | My Voyage"): returned with its separator, else ""."""
    if len(names) < 3:
        return ""
    count: Dict[str, int] = {}
    for n in names:
        for sep in SEPARATORS:
            if sep in n:
                tail = sep + n.rsplit(sep, 1)[1].strip()
                count[tail] = count.get(tail, 0) + 1
    if not count:
        return ""
    tail, n = max(count.items(), key=lambda x: x[1])
    return tail if n >= 3 and n >= 0.6 * len(names) and len(tail) > 3 else ""


def page_text(content: str, name) -> str:
    """Paragraphs of the page without button / widget lines, bare URLs and the name repeated at the top (name: one name or several)."""
    names = {name} if isinstance(name, str) else set(name)
    lines = [l.strip() for l in (content or "").split("\n")]
    out: List[str] = []
    for l in lines:
        if not l or is_ui_line(l):
            continue
        if not out and l in names:      # the heading is printed once, as the page name
            continue
        out.append(l)
    return "\n\n".join(out)


def export_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    full = [page_name(p) for p in pages]
    tail = common_suffix(full)
    items = []
    for p, name in zip(pages, full):
        short = name[: -len(tail)].strip() if tail and name.endswith(tail) and len(name) > len(tail) else name
        text = page_text(p.get("content") or "", {name, short})
        if text:
            items.append({"page": short, "content": text})
    return items
