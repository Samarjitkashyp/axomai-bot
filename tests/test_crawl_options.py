# -*- coding: utf-8 -*-
"""Tests of the three crawl-form options (Include Subdomains, Force Re-crawl / Smart Skip, Render JavaScript) and of the
profile lock on subdomains. They crawl a tiny local web server, never a real site. Run from the bot folder:
    venv/bin/python3 tests/test_crawl_options.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from crawler import cleaner  # noqa: E402
from crawler.catalog import URLCatalog  # noqa: E402
from crawler.engine import CrawlerEngine, CrawlerJob  # noqa: E402
from crawler.frontier import URLFrontier  # noqa: E402
from crawler.storage import StorageManager  # noqa: E402

PAGES = {
    "/": '<html><head><title>Home</title></head><body><main><h1>Home</h1><p>Welcome to the little test site with enough words to count as real content.</p>'
         '<a href="/a">A</a> <a href="/b">B</a> <a href="/js">JS</a></main></body></html>',
    "/a": '<html><head><title>Page A</title></head><body><main><h1>Page A</h1><p>Alpha page text that is long enough to be real content for the crawler tests.</p></main></body></html>',
    "/b": '<html><head><title>Page B</title></head><body><main><h1>Page B</h1><p>Beta page text that is long enough to be real content for the crawler tests.</p></main></body></html>',
    "/js": '<html><head><title>JS page</title></head><body><main><h1>JS page</h1><div id="x">loading</div>'
           '<script>document.getElementById("x").textContent="text made by javascript only";</script></main></body></html>',
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def crawl(seed, tmp, force=False, render_js=False, subdomains=False, catalog=None):
    job = CrawlerJob(job_id="job_t%d" % len(os.listdir(tmp)), seed_url=seed, max_pages=50, max_depth=3, crawl_delay=0, render_js=render_js,
                     force_recrawl=force, include_subdomains=subdomains)
    sm = StorageManager(output_dir=os.path.join(tmp, "crawled_data"))
    engine = CrawlerEngine(job=job, storage_manager=sm, catalog=catalog or URLCatalog(catalog_path=os.path.join(tmp, "catalog.json")))
    asyncio.run(engine.run())
    return job


# ---------------------------------------------------------------- Include Subdomains
def accepts(seed, url, subdomains):
    return URLFrontier(seed, include_subdomains=subdomains).is_same_domain(url)


def test_subdomains_off_stays_on_the_seed_host():
    assert accepts("https://assam.news18.com/", "https://assam.news18.com/news/x.html", False)
    assert not accepts("https://assam.news18.com/", "https://hindi.news18.com/", False)
    assert not accepts("https://assam.news18.com/", "https://www.news18.com/", False)


def test_subdomains_on_news18_takes_sister_sites():
    assert accepts("https://assam.news18.com/", "https://hindi.news18.com/x", True)
    assert not accepts("https://assam.news18.com/", "https://example.com/", True)


def test_subdomains_on_does_not_cross_all_of_gov_in():
    # the old code took the last two labels ("gov.in") as the domain: every .gov.in site was "the same site"
    assert accepts("https://assam.gov.in/", "https://dibrugarh.assam.gov.in/x", True)
    assert not accepts("https://assam.gov.in/", "https://kerala.gov.in/", True)
    assert not accepts("https://assamtourism.gov.in/", "https://www.india.gov.in/", True)


def test_subdomains_on_does_not_cross_all_of_co_in():
    assert accepts("https://myvoyage.co.in/", "https://blog.myvoyage.co.in/", True)
    assert not accepts("https://myvoyage.co.in/", "https://someone-else.co.in/", True)


def test_subdomains_on_other_two_part_suffixes():
    assert accepts("https://www.example.co.uk/", "https://blog.example.co.uk/", True)
    assert not accepts("https://www.example.co.uk/", "https://other.co.uk/", True)
    assert accepts("https://www.assamtribune.com/", "https://epaper.assamtribune.com/", True)


# ---------------------------------------------------------------- the profile lock on subdomains
def test_profile_can_lock_subdomains_off():
    srv = serve()
    seed = "http://127.0.0.1:%d/" % srv.server_address[1]
    with tempfile.TemporaryDirectory() as tmp:
        old = cleaner.PROFILE_FILE
        cleaner.PROFILE_FILE = os.path.join(tmp, "p.json")
        json.dump({"127.0.0.1:%d" % srv.server_address[1]: {"allow_subdomains": False}}, open(cleaner.PROFILE_FILE, "w"))
        try:
            job = CrawlerJob("job_lock", seed, 5, 2, 0, False, include_subdomains=True)
            CrawlerEngine(job=job, storage_manager=StorageManager(output_dir=os.path.join(tmp, "c")), catalog=URLCatalog(catalog_path=os.path.join(tmp, "cat.json")))
            assert job.include_subdomains is False
            other = CrawlerJob("job_nolock", seed.replace("127.0.0.1", "localhost"), 5, 2, 0, False, include_subdomains=True)
            CrawlerEngine(job=other, storage_manager=StorageManager(output_dir=os.path.join(tmp, "c")), catalog=URLCatalog(catalog_path=os.path.join(tmp, "cat.json")))
            assert other.include_subdomains is True  # no profile, no lock
        finally:
            cleaner.PROFILE_FILE = old
    srv.shutdown()


# ---------------------------------------------------------------- Force Re-crawl / Smart Skip
def test_smart_skip_and_force_recrawl():
    srv = serve()
    seed = "http://127.0.0.1:%d/" % srv.server_address[1]
    with tempfile.TemporaryDirectory() as tmp:
        cat = URLCatalog(catalog_path=os.path.join(tmp, "catalog.json"))
        first = crawl(seed, tmp, catalog=cat)
        assert first.pages_new == 4 and first.pages_skipped == 0
        second = crawl(seed, tmp, catalog=cat)                      # nothing changed, Smart Skip on
        assert second.pages_skipped == 4 and second.pages_new == 0
        assert {p["change_status"] for p in second.pages} == {"skipped_unchanged"}
        assert len(second.pages) == 4                               # the pages are still saved (so cleaning sees the whole site)
        third = crawl(seed, tmp, force=True, catalog=cat)           # Force Re-crawl: nothing is skipped
        assert third.pages_skipped == 0 and third.pages_new == 4
        assert len(third.pages) == 4
        PAGES["/a"] = PAGES["/a"].replace("Alpha page text", "Alpha page text, now changed,")
        try:
            fourth = crawl(seed, tmp, catalog=cat)
            assert fourth.pages_updated == 1 and fourth.pages_skipped == 3
        finally:
            PAGES["/a"] = PAGES["/a"].replace("Alpha page text, now changed,", "Alpha page text")
    srv.shutdown()


# ---------------------------------------------------------------- Render JavaScript
def test_render_javascript():
    srv = serve()
    seed = "http://127.0.0.1:%d/" % srv.server_address[1]
    with tempfile.TemporaryDirectory() as tmp:
        plain = crawl(seed, tmp)
        text = " ".join(p["content"] for p in plain.pages if p["url"].endswith("/js"))
        assert "made by javascript" not in text and "loading" in text
        rendered = crawl(seed, tmp, render_js=True, catalog=URLCatalog(catalog_path=os.path.join(tmp, "c2.json")))
        text = " ".join(p["content"] for p in rendered.pages if p["url"].endswith("/js"))
        assert "text made by javascript only" in text, text
        assert rendered.pages_crawled == 4 and not rendered.errors
    srv.shutdown()


if __name__ == "__main__":
    n = 0
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok    ", name)
                n += 1
            except Exception as e:
                print("FAILED", name, "->", type(e).__name__, str(e)[:120])
                failed.append(name)
    print("%d passed, %d failed" % (n, len(failed)))
    sys.exit(1 if failed else 0)
