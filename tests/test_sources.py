# -*- coding: utf-8 -*-
"""Tests for crawler/sources.py (WordPress API source). Run from the bot folder:  python3 tests/test_sources.py"""
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from crawler import sources  # noqa: E402

sources.API_DELAY = 0
BODY = "<p>" + " ".join("word%d" % i for i in range(45)) + "</p><p>Second paragraph about Kaziranga national park and its rhinos.</p>"


def item(i, kind="posts", protected=False, body=BODY):
    return {"link": "https://wp.example/%s-%d/" % (kind, i), "title": {"rendered": "Title &amp; %d" % i}, "content": {"rendered": body, "protected": protected}, "type": kind}


def site(types=None, posts=3, pages=1, protected_post=False, body=BODY, status=200):
    types = types if types is not None else {"page": {"rest_base": "pages"}, "post": {"rest_base": "posts"}, "attachment": {"rest_base": "media"}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if status != 200:
            return httpx.Response(status)
        if path.endswith("/types"):
            return httpx.Response(200, json=types)
        if path.endswith("/pages"):
            return httpx.Response(200, json=[item(i, "pages", body=body) for i in range(pages)] if request.url.params.get("page") == "1" else [])
        if path.endswith("/posts"):
            rows = [item(i, "posts", protected=(protected_post and i == 0), body=body) for i in range(posts)]
            return httpx.Response(200, json=rows if request.url.params.get("page") == "1" else [])
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(client, seed="https://wp.example/", max_pages=100):
    async def go():
        async with client:
            return await sources.wordpress_pages(client, seed, max_pages)
    return asyncio.run(go())


def test_pages_come_first_then_posts_and_text_is_clean():
    out = run(site())
    assert [p["url"] for p in out][0] == "https://wp.example/pages-0/"
    assert len(out) == 4
    p = out[1]
    assert p["title"] == "Title & 0"                                  # html entities decoded
    assert p["content"].startswith("Title & 0")                       # the page name first
    assert "<p>" not in p["content"] and "Second paragraph about Kaziranga" in p["content"]
    assert p["discovered_links"] == [] and p["word_count"] > 40


def test_attachment_type_is_not_fetched_and_protected_posts_are_skipped():
    out = run(site(protected_post=True))
    assert len(out) == 3 and all("/media" not in p["url"] for p in out)
    assert "https://wp.example/posts-0/" not in [p["url"] for p in out]


def test_max_pages_is_respected():
    assert len(run(site(posts=50), max_pages=5)) == 5


def test_not_wordpress_or_error_means_normal_crawl():
    assert run(site(status=404)) == []
    assert run(site(status=403)) == []


def test_section_of_a_site_is_a_normal_crawl():
    assert run(site(), seed="https://wp.example/blog/") == []


def test_too_little_real_text_means_normal_crawl():
    assert run(site(body="<p>Short text only here ok</p>")) == []
    assert run(site(posts=1, pages=1)) == []          # fewer than 3 real pages


def test_builder_types_and_utility_pages_are_skipped():
    asked = []
    types = {"page": {"rest_base": "pages"}, "post": {"rest_base": "posts"}, "elementor_library": {"rest_base": "elementor-lib"}, "rank_math_schema": {"rest_base": "schemas"}}

    def handler(request):
        asked.append(request.url.path)
        if request.url.path.endswith("/types"):
            return httpx.Response(200, json=types)
        page = request.url.params.get("page") == "1"
        if request.url.path.endswith("/pages"):
            rows = [dict(item(0, "pages"), link="https://wp.example/%s/" % s) for s in ("reset-password", "users", "about-us", "homepage-2")]
            return httpx.Response(200, json=rows if page else [])
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json=[item(i) for i in range(3)] if page else [])
        return httpx.Response(404)
    out = run(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert not any("elementor-lib" in a or "schemas" in a for a in asked)
    assert sorted(p["url"] for p in out if "/posts-" not in p["url"]) == ["https://wp.example/about-us/"]


def test_pages_do_not_crowd_out_the_posts():
    out = run(site(posts=20, pages=20), max_pages=10)
    assert len(out) == 10 and sum(1 for p in out if "/posts-" in p["url"]) == 7


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
