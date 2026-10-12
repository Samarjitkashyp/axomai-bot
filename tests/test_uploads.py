# -*- coding: utf-8 -*-
"""Tests for app/doc_ocr.py and app/uploads.py (uploaded documents -> text -> review). Run from the bot folder:  python3 tests/test_uploads.py"""
import asyncio
import os
import sys
import tempfile

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import doc_ocr, uploads as up  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
ASSAMESE = "অসম এখন ভাৰতৰ উত্তৰ-পূব অঞ্চলৰ এটা ৰাজ্য আৰু ইয়াৰ ৰাজধানী দিছপুৰ"
ENGLISH = "Assam is a state in northeastern India and its capital is Dispur"

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# ---- file types are decided by the bytes, not by the name
check("png", doc_ocr.detect_kind(PNG) == "png")
check("jpeg", doc_ocr.detect_kind(JPG) == "jpeg")
check("pdf", doc_ocr.detect_kind(PDF) == "pdf")
check("webp", doc_ocr.detect_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp")
check("exe is not allowed", doc_ocr.detect_kind(b"MZ\x90\x00" + b"\x00" * 64) is None)
check("html renamed .jpg is not allowed", doc_ocr.detect_kind(b"<html><script>alert(1)</script>") is None)

# ---- text tidy-up and the review hint
check("clean keeps Assamese", doc_ocr.clean_text("  " + ASSAMESE + "\x00\n\n\n\n  x ") == ASSAMESE + "\n\nx")
check("agreement same text", doc_ocr.agreement(ENGLISH, ENGLISH) == 1.0)
check("agreement different text", doc_ocr.agreement(ENGLISH, "zzz yyy xxx www vvv") == 0.0)
check("empty page is flagged", doc_ocr.judge("", "gemini")[0])
check("tesseract is always flagged", doc_ocr.judge(ENGLISH, "tesseract")[0])
check("good gemini page is not flagged", not doc_ocr.judge(ENGLISH, "gemini", ENGLISH)[0])
check("disagreeing readers are flagged", doc_ocr.judge(ENGLISH, "gemini", "qqq www eee rrr ttt yyy")[0])
check("no cross-check, good page", not doc_ocr.judge(ENGLISH, "openai", None)[0])

# ---- creating an upload
base = tempfile.mkdtemp()
check("nothing chosen", raises(lambda: up.create_upload([], "u", base), up.UploadError))
check("not an image", raises(lambda: up.create_upload([("a.jpg", b"hello world")], "u", base), up.UploadError))
check("empty file", raises(lambda: up.create_upload([("a.png", b"")], "u", base), up.UploadError))
check("over 20 MB", raises(lambda: up.create_upload([("a.png", PNG + b"0" * (20 * 1024 * 1024))], "u", base), up.UploadError))
check("pdf + picture together", raises(lambda: up.create_upload([("a.pdf", PDF), ("b.png", PNG)], "u", base), up.UploadError))
check("too many pictures", raises(lambda: up.create_upload([("%d.png" % i, PNG) for i in range(up.MAX_FILES + 1)], "u", base), up.UploadError))
check("bad id is refused", raises(lambda: up.doc_dir("../../etc", base), ValueError))
m = up.create_upload([("../../evil name.png", PNG)], "admin", base)
check("created", m["status"] == "processing" and up.read_meta(m["id"], base)["uploader"] == "admin")
check("file name is stripped of its path", "/" not in m["files"][0]["name"] and ".." not in m["files"][0]["name"])
check("original stored inside its own folder", os.path.isfile(os.path.join(base, m["id"], "orig1.png")))
check("resume lists unfinished", m["id"] in up.resume_unfinished(base))

# ---- reading, with fake engines
calls = {"gemini": 0, "openai": 0, "tess": 0}
mode = {"gemini": "ok", "openai": "ok", "tess": ENGLISH}


async def fake_gemini(data, mime, client, key=None):
    calls["gemini"] += 1
    if mode["gemini"] == "quota":
        raise doc_ocr.EngineUnavailable("gemini: HTTP 429")
    return ASSAMESE


async def fake_openai(data, mime, client, key=None):
    calls["openai"] += 1
    if mode["openai"] == "no":
        raise doc_ocr.EngineUnavailable("no OPENAI_API_KEY")
    return ASSAMESE + " (openai)"


def fake_tess(path):
    calls["tess"] += 1
    return mode["tess"]


doc_ocr.gemini_read, doc_ocr.openai_read, doc_ocr.tesseract_read = fake_gemini, fake_openai, fake_tess
up.daily_limit = lambda: 500
client = httpx.AsyncClient()

# a. Gemini works; Tesseract disagrees (it cannot read Assamese) -> flagged
mode["tess"] = "qqq www eee rrr ttt yyy"
r = run(up.process_upload(m["id"], base, client))
check("moves to review", r["status"] == "review" and r["page_count"] == 1)
check("gemini used", r["pages"][0]["engine"] == "gemini" and r["pages"][0]["text"] == ASSAMESE)
check("openai not called when gemini works", calls["openai"] == 0)
check("disagreement flagged", r["pages"][0]["review"] is True)
check("paid page counted", up.pages_used_today(base) == 1)

# b. Gemini quota used up -> OpenAI
m2 = up.create_upload([("b.png", PNG), ("c.jpg", JPG)], "admin", base)
mode["gemini"], mode["tess"] = "quota", ASSAMESE
r2 = run(up.process_upload(m2["id"], base, client))
check("two pictures = two pages", r2["page_count"] == 2 and len(r2["pages"]) == 2)
check("openai takes over", all(p["engine"] == "openai" for p in r2["pages"]))
check("name says (+1 more)", "+1 more" in r2["name"])

# c. neither paid engine -> Tesseract, flagged
m3 = up.create_upload([("d.png", PNG)], "admin", base)
mode["openai"], mode["tess"] = "no", ENGLISH
r3 = run(up.process_upload(m3["id"], base, client))
check("tesseract last", r3["pages"][0]["engine"] == "tesseract" and r3["pages"][0]["review"] and r3["status"] == "review")

# d. daily limit reached -> paid engines are not called at all
m4 = up.create_upload([("e.png", PNG)], "admin", base)
mode["gemini"] = "ok"
up.daily_limit = lambda: up.pages_used_today(base)
before = (calls["gemini"], calls["openai"])
r4 = run(up.process_upload(m4["id"], base, client))
check("limit: no paid call", (calls["gemini"], calls["openai"]) == before)
check("limit: tesseract + flagged", r4["pages"][0]["engine"] == "tesseract" and r4["pages"][0]["review"] and "limit" in r4["pages"][0]["reason"])
up.daily_limit = lambda: 500

# e. nothing readable -> failed
m5 = up.create_upload([("f.png", PNG)], "admin", base)
mode["gemini"], mode["openai"], mode["tess"] = "quota", "no", ""
r5 = run(up.process_upload(m5["id"], base, client))
check("no text = failed with a message", r5["status"] == "failed" and r5["error"])

# f. processing an upload twice does nothing the second time
check("second run does not re-read", run(up.process_upload(m["id"], base, client))["status"] == "review")

# ---- a PDF: a page with a text layer is exact and free; a scanned page is read as a picture
mp = up.create_upload([("g.pdf", PDF)], "admin", base)
layer = {1: ENGLISH + " " + ENGLISH, 2: ""}
doc_ocr.pdf_page_count = lambda path: 2
doc_ocr.pdf_text_layer = lambda path, n: layer[n]


def fake_render(path, n, out, dpi=150):
    with open(out + ".jpg", "wb") as f:
        f.write(JPG)
    return out + ".jpg"


doc_ocr.pdf_render = fake_render
mode["gemini"] = "ok"
tess_calls = calls["tess"]
rp = run(up.process_upload(mp["id"], base, client))
check("pdf: text layer page", rp["pages"][0]["engine"] == "pdf-text" and not rp["pages"][0]["review"] and rp["pages"][0]["image"] == "")
check("pdf: scanned page read by gemini", rp["pages"][1]["engine"] == "gemini" and rp["pages"][1]["image"] == "p2.jpg")
check("pdf: the text-layer page did not use any reader", calls["tess"] == tess_calls + 1)
doc_ocr.pdf_page_count = lambda path: doc_ocr.MAX_PDF_PAGES + 1
mp2 = up.create_upload([("h.pdf", PDF)], "admin", base)
check("pdf over the page limit fails clearly", run(up.process_upload(mp2["id"], base, client))["status"] == "failed")

# ---- review: edit, approve, build pages
check("edit needs review status", raises(lambda: up.edit_page(r4["id"], 1, "x", base) if False else up.edit_page(r5["id"], 1, "x", base), up.UploadError))
e = up.edit_page(m["id"], 1, "  " + ENGLISH + "\x00 ", base)
check("edit stores clean text and clears the flag", e["pages"][0]["text"] == ENGLISH and e["pages"][0]["review"] is False and e["pages"][0]["edited"])
bp = up.build_pages(up.read_meta(m2["id"], base))
check("pages for the chat import", len(bp) == 2 and bp[0]["url"].endswith("#p1") and bp[0]["url"].startswith("https://documents.aiaxom.co.in/"))
check("page titles carry the file name and number", "page 2" in bp[1]["title"])
check("single page document keeps the plain name", up.build_pages(up.read_meta(m["id"], base))[0]["title"] == up.read_meta(m["id"], base)["name"])
check("empty pages are left out", up.build_pages({"id": "x", "name": "n", "pages": [{"n": 1, "text": " "}]}) == [])
ap = up.mark(m["id"], "approved", "admin", base, job_id="doc-" + m["id"])
check("approved", ap["status"] == "approved" and ap["job_id"] == "doc-" + m["id"] and ap["reviewed_by"] == "admin")
check("approving marks the send as in progress", ap["send_status"] == "sending")
up.set_send_status(m["id"], False, base)
check("a failed send is recorded (admin can send again)", up.read_meta(m["id"], base)["send_status"] == "failed")
up.set_send_status(m["id"], True, base)
check("a successful send is recorded", up.read_meta(m["id"], base)["send_status"] == "sent" and up.list_uploads(base)[0].get("send_status") in ("sent", "failed", "sending", None))
up.mark(m["id"], "approved", "admin", base)
check("sending again starts from sending", up.read_meta(m["id"], base)["send_status"] == "sending")
check("cannot reject twice from failed", raises(lambda: up.mark(r5["id"], "rejected", "a", base), up.UploadError))
check("listing", len(up.list_uploads(base)) >= 6 and up.list_uploads(base)[0]["created_at"] >= up.list_uploads(base)[-1]["created_at"])

# ---- delete
check("delete", up.delete_upload(m5["id"], base) and up.read_meta(m5["id"], base) is None)
check("delete missing", up.delete_upload("abcdef012345", base) is False)

run(client.aclose())
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
