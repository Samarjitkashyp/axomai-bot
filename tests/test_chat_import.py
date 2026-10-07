# -*- coding: utf-8 -*-
"""Tests for app/chat_import.py (the batched, retrying import into Axom AI), against a fake Axom AI on localhost.
Run from the bot folder:  venv/bin/python3 tests/test_chat_import.py"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.chat_import import push_pages  # noqa: E402


def fake_axom_ai(script):
    """script: list of status codes to answer with, in order (then 200)."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            code = script.pop(0) if script else 200
            seen.append({"token": self.headers.get("X-Bot-Token"), "n": len(body["pages"]), "seed": body["seed_url"], "code": code})
            out = json.dumps({"success": True, "created": len(body["pages"]) if code == 200 else 0, "updated": 0, "embedded": len(body["pages"]) if code == 200 else 0}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def pages(n):
    return [{"url": "https://x.in/p%d" % i, "title": "t%d" % i, "content": "some text %d" % i} for i in range(n)]


def run(script, n, **kw):
    srv, seen = fake_axom_ai(script)
    try:
        url = "http://127.0.0.1:%d/api/import-crawl/" % srv.server_address[1]
        return asyncio.run(push_pages(url, "secret-token", "https://x.in/", "job_1", pages(n), backoff=(0, 0, 0), **kw)), seen
    finally:
        srv.shutdown()


def test_pages_go_in_batches_of_200():
    totals, seen = run([], 450)
    assert [r["n"] for r in seen] == [200, 200, 50]
    assert totals["created"] == 450 and totals["batches"] == 3 and totals["failed_batches"] == 0
    assert all(r["token"] == "secret-token" for r in seen)


def test_a_failed_batch_is_retried():
    totals, seen = run([500, 500], 250)       # the first batch fails twice, then works
    assert totals["created"] == 250 and totals["failed_batches"] == 0
    assert [r["code"] for r in seen] == [500, 500, 200, 200]


def test_gives_up_after_three_tries_and_goes_on_with_the_next_batch():
    totals, seen = run([500, 500, 500], 250)
    assert totals["failed_batches"] == 1 and totals["created"] == 50 and totals["batches"] == 2


def test_wrong_token_stops_everything():
    totals, seen = run([401], 450)
    assert totals["aborted"] is True and len(seen) == 1 and totals["created"] == 0


def test_pages_without_text_are_not_sent():
    srv, seen = fake_axom_ai([])
    try:
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        totals = asyncio.run(push_pages(url, "t", "https://x.in/", "j", [{"url": "a", "title": "a", "content": "  "}, {"url": "b", "title": "b", "content": "text"}]))
        assert totals["pages"] == 1 and seen[0]["n"] == 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
            n += 1
    print("%d tests passed" % n)
