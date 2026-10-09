# -*- coding: utf-8 -*-
"""Tests for app/qa_chunks.py (passages + AI questions). Run from the bot folder:  python3 tests/test_qa_chunks.py"""
import asyncio
import json
import os
import sys
import tempfile

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import qa_chunks as qc  # noqa: E402

SEED = "https://site.example/"


def para(tag, n=60):
    return " ".join("%s%d" % (tag, i) for i in range(n))


def page(url, title, paras, status="new"):
    return {"url": url, "title": title, "content": "\n\n".join([title] + paras), "change_status": status}


async def good_ask(prompt):
    """A fake AI: one question per [id n] in the prompt."""
    ids = [int(x) for x in __import__("re").findall(r"\[id (\d+)\]", prompt)]
    return [{"id": i, "question": "What does passage number %d say about the service?" % i} for i in ids]


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- passages
def test_passages_are_cut_at_paragraphs_and_a_short_tail_is_joined():
    text = "\n\n".join([para("a"), para("b"), para("c"), para("d")])           # 4 x 60 words
    chunks = qc.split_chunks(text)
    assert [len(c.split()) for c in chunks] == [180, 60] or all(40 <= len(c.split()) <= 240 for c in chunks)
    assert "".join(chunks).count("a0") == 1 and sum(len(c.split()) for c in chunks) == 240
    tail = qc.split_chunks("\n\n".join([para("a", 170), para("b", 10)]))        # a 10-word tail is joined, not left alone
    assert len(tail) == 1 and len(tail[0].split()) == 180
    assert qc.split_chunks("two words") == []                                   # tiny text is not a passage


def test_valid_question():
    assert qc.valid_question("What services does the company offer?")
    assert not qc.valid_question("short?") and not qc.valid_question("no question mark here at all") and not qc.valid_question(None)


# ---------------------------------------------------------------- the build
def test_each_passage_gets_its_own_question_and_url():
    pages = [page("https://site.example/seo", "SEO | Site", [para("s", 100), para("t", 100), para("u", 100)]),
             page("https://site.example/about", "About | Site", [para("x", 90)]),
             page("https://site.example/contact", "Contact | Site", [para("y", 90)])]
    built = run(qc.build_qa_pages(pages, SEED, good_ask))
    urls = [p["url"] for p in built["qa"]]
    assert urls[0] == "https://site.example/seo#c1" and "https://site.example/seo#c2" in urls and "https://site.example/about#c1" in urls
    assert all(p["title"].startswith("What does passage") for p in built["qa"])
    assert built["qa"][0]["content"].startswith("SEO\n\n")                      # the page name (without the common site suffix) opens the answer
    assert built["plain"] == [] and built["stats"]["ai_questions"] == len(built["qa"]) and built["stats"]["fallback_questions"] == 0
    assert sorted(built["bases"]) == ["https://site.example/about", "https://site.example/contact", "https://site.example/seo"]


def test_a_passage_without_a_usable_question_gets_a_fallback_and_is_not_lost():
    calls = []

    async def stingy(prompt):
        calls.append(prompt)
        return [{"id": 0, "question": "no"}]                                     # not a valid question: asked again, then fallback
    built = run(qc.build_qa_pages([page("https://site.example/a", "A", [para("w", 100)])], SEED, stingy))
    assert len(built["qa"]) == 1 and built["stats"]["fallback_questions"] == 1 and len(calls) == 2
    assert built["qa"][0]["title"].startswith("A: w0 w1")


def test_when_the_ai_is_unavailable_every_page_stays_a_whole_page():
    async def down(prompt):
        raise qc.QAUnavailable("quota")
    pages = [page("https://site.example/a", "A", [para("w", 100)]), page("https://site.example/b", "B", [para("v", 100)])]
    built = run(qc.build_qa_pages(pages, SEED, down))
    assert built["qa"] == [] and built["plain"] == pages and built["stats"]["ai_unavailable"]


def test_passage_limit_and_pages_without_text():
    pages = [page("https://site.example/%d" % i, "P%d" % i, [para("w%d" % i, 100)]) for i in range(5)] + [page("https://site.example/empty", "Empty", ["More"])]
    built = run(qc.build_qa_pages(pages, SEED, good_ask, max_passages=3))
    assert len(built["qa"]) == 3 and len(built["plain"]) == 3 and built["plain"][-1]["url"].endswith("/empty")


# ---------------------------------------------------------------- the old rows
def test_rows_to_replace_and_unchanged_pages():
    existing = [(1, "https://s/a"), (2, "https://s/a#c1"), (3, "https://s/a#c2"), (4, "https://s/b"), (5, "https://s/c#c1")]
    assert sorted(qc.rows_to_replace(existing, ["https://s/a", "https://s/c"])) == [1, 2, 3, 5]
    pages = [{"url": "https://s/c", "change_status": "skipped_unchanged"}, {"url": "https://s/b", "change_status": "skipped_unchanged"},
             {"url": "https://s/a", "change_status": "updated"}]
    assert qc.unchanged_with_passages(pages, existing) == ["https://s/c"]        # b is unchanged but has only the old whole-page row: it is converted


class FakeCursor:
    def __init__(self, db):
        self.db, self.result = db, []

    def execute(self, sql, params=None):
        self.db.log.append((sql.split()[0], params))
        if "FROM knowledge_knowledgedocument" in sql:
            self.result = [(7,)] if self.db.doc else []
        elif "SELECT id, source_url" in sql:
            self.result = [(i, u) for i, u in self.db.rows.items()]
        elif "SELECT id, question" in sql:
            self.result = [(i, "q%d" % i, "a%d" % i, "", "Web: s", self.db.rows[i]) for i in params[1] if i in self.db.rows]
        elif sql.startswith("DELETE"):
            for i in params[1]:
                self.db.rows.pop(i, None)

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def close(self):
        pass


class FakeDB:
    def __init__(self, rows=None, doc=True):
        self.rows, self.doc, self.log, self.closed = dict(rows or {}), doc, [], False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def close(self):
        self.closed = True


def pusher():
    sent = []

    async def push(batch):
        sent.append([p["url"] for p in batch])
        return {"pages": len(batch), "failed_batches": 0, "aborted": False, "created": len(batch), "updated": 0, "embedded": len(batch)}
    return push, sent


def test_whole_step_replaces_old_rows_after_a_backup_and_imports_the_passages():
    db = FakeDB({1: "https://site.example/a", 2: "https://site.example/a#c1", 3: "https://site.example/other"})
    push, sent = pusher()
    with tempfile.TemporaryDirectory() as d:
        out = run(qc.push_with_passages(push, SEED, "job1", [page("https://site.example/a", "A", [para("w", 100)])], lambda: db, good_ask, d))
        assert out["mode"] == "passages" and out["replaced_rows"] == 2 and out["backup"]
        saved = json.load(open(out["backup"], encoding="utf-8"))
        assert sorted(r["id"] for r in saved["rows"]) == [1, 2] and saved["document_id"] == 7
    assert db.rows == {3: "https://site.example/other"}                          # only the rows of the crawled page went
    assert sent == [["https://site.example/a#c1"]] and db.closed


def test_ai_down_means_the_old_import_and_nothing_deleted():
    db = FakeDB({1: "https://site.example/a"})
    push, sent = pusher()

    async def down(prompt):
        raise qc.QAUnavailable("no key")
    pages = [page("https://site.example/a", "A", [para("w", 100)])]
    out = run(qc.push_with_passages(push, SEED, "job1", pages, lambda: db, down, tempfile.gettempdir()))
    assert out["mode"] == "pages" and sent == [["https://site.example/a"]] and db.rows == {1: "https://site.example/a"}


def test_failed_passage_import_falls_back_to_whole_pages():
    db = FakeDB({1: "https://site.example/a"})
    sent = []

    async def flaky(batch):
        sent.append([p["url"] for p in batch])
        return {"failed_batches": 1 if len(sent) == 1 else 0, "aborted": False}
    out = run(qc.push_with_passages(flaky, SEED, "job1", [page("https://site.example/a", "A", [para("w", 100)])], lambda: db, good_ask, tempfile.gettempdir()))
    assert out["mode"] == "pages_after_failure" and sent == [["https://site.example/a#c1"], ["https://site.example/a"]]


def test_unchanged_page_with_passages_is_left_alone_and_a_dead_database_means_the_old_import():
    db = FakeDB({2: "https://site.example/a#c1"})
    push, sent = pusher()
    out = run(qc.push_with_passages(push, SEED, "job1", [page("https://site.example/a", "A", [para("w", 100)], status="skipped_unchanged")], lambda: db, good_ask, tempfile.gettempdir()))
    assert out["mode"] == "passages" and out["skipped_unchanged"] == 1 and sent == [] and db.rows == {2: "https://site.example/a#c1"}

    def broken():
        raise RuntimeError("db down")
    push2, sent2 = pusher()
    out = run(qc.push_with_passages(push2, SEED, "job1", [page("https://site.example/a", "A", [para("w", 100)])], broken, good_ask, tempfile.gettempdir()))
    assert out["mode"] == "pages_after_error" and sent2 == [["https://site.example/a"]]


def test_switches():
    old = os.environ.get("AXOMAI_QA_CHUNKS")
    try:
        os.environ["AXOMAI_QA_CHUNKS"] = "0"
        assert qc.qa_enabled(SEED) is False
        os.environ["AXOMAI_QA_CHUNKS"] = "1"
        assert qc.qa_enabled(SEED) is True
    finally:
        if old is None:
            os.environ.pop("AXOMAI_QA_CHUNKS", None)
        else:
            os.environ["AXOMAI_QA_CHUNKS"] = old


# ---------------------------------------------------------------- Gemini client
def gemini(handler, key="k", model="gemini-old-flash-lite"):
    return qc.Gemini(key, model, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok_body(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_moves_to_a_discovered_model_when_the_configured_one_is_gone():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"name": "models/gemini-9.9-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                                                        {"name": "models/gemini-9.9-flash-tts", "supportedGenerationMethods": ["generateContent"]}]})
        if "gemini-old-flash-lite" in request.url.path:
            return httpx.Response(404, json={"error": "gone"})
        return httpx.Response(200, json=ok_body('[{"id": 0, "question": "A fine question here?"}]'))
    g = gemini(handler)
    res = run(g.ask("p"))
    assert res == [{"id": 0, "question": "A fine question here?"}] and any("gemini-9.9-flash-lite" in s for s in seen) and not any("tts" in s for s in seen)


def test_gemini_with_no_key_or_all_models_over_quota_is_unavailable():
    try:
        run(gemini(lambda r: httpx.Response(200), key="").ask("p"))
        assert False
    except qc.QAUnavailable:
        pass
    try:
        run(gemini(lambda r: httpx.Response(429, json={"error": "quota"}) if "generateContent" in r.url.path else httpx.Response(200, json={"models": []})).ask("p"))
        assert False
    except qc.QAUnavailable:
        pass


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
