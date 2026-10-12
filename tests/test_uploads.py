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
check("html line breaks from the AI become real line breaks", doc_ocr.clean_text("one<br>two<BR/>three<br />four") == "one\ntwo\nthree\nfour")
check("agreement same text", doc_ocr.agreement(ENGLISH, ENGLISH) == 1.0)
check("agreement different text", doc_ocr.agreement(ENGLISH, "zzz yyy xxx www vvv") == 0.0)
check("an empty page read by the AI is not a problem (picture-only / blank page)", not doc_ocr.judge("", "gemini")[0])
check("an empty page read by Tesseract is flagged", doc_ocr.judge("", "tesseract")[0])
check("two words from the AI are flagged", doc_ocr.judge("Hello there", "gemini")[0])
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
doc_ocr.pdf_image_pixels = lambda path: {2: 2_000_000}


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


# ---- a PDF whose own text is unreadable (old Assamese font), pictures with text, blank pages, repeated headers
MOJIBAKE = "ø¬ıÚ±˜”˘œ˚˛± ¬Û±Í¬…¬Û≈øÔ ’—ﬂ≈¡1Ì [õ∂Ô˜ ˆ¬±·] ø˙é¬± [õ∂±Ôø˜ﬂ¡] ø¬ıˆ¬±· ’¸˜ ‰¬1ﬂ¡±1"
WATERMARK = "C\nop\nno yr\nt igh\nto t\n-\nbe SC\nre ER\npu T\nbl A,\nis ss\nhe a\nd m"
check("layer: good English", doc_ocr.layer_quality(ENGLISH + ". " + ENGLISH) == "good")
check("layer: good Unicode Assamese", doc_ocr.layer_quality(ASSAMESE + " " + ASSAMESE) == "good")
check("layer: old-font Assamese is garbled", doc_ocr.layer_quality(MOJIBAKE) == "garbled")
check("layer: a rotated watermark only is garbled", doc_ocr.layer_quality(WATERMARK) == "garbled")
check("layer: accents in a French text are fine", doc_ocr.layer_quality("Le ministere de l'education a publie un nouveau manuel scolaire pour les eleves de l'ecole primaire a Paris, d'apres l'ete dernier. Tres bien.") == "good")
check("layer: a few accented words are fine", doc_ocr.layer_quality("The caf\u00e9 near the r\u00e9sum\u00e9 office serves na\u00efve students and their fianc\u00e9s every morning before class") == "good")
check("layer: too short is empty", doc_ocr.layer_quality("Page 4") == "empty")

heads = ["Class 1 Assamese Textbook SCERT"] * 8
texts = ["%s\nParagraph number %d is real content of the book.\n%d" % (heads[i], i, i + 1) for i in range(8)]
cleaned, removed = doc_ocr.strip_repeated_lines(texts)
check("repeated header and bare page numbers are removed", removed == 16 and all("Textbook" not in t for t in cleaned) and all("Paragraph number %d" % i in cleaned[i] for i in range(8)))
check("a short document is left alone", doc_ocr.strip_repeated_lines(texts[:3]) == (texts[:3], 0))
keep = ["Chapter one\nThe price is 250 rupees\n250", "Chapter two\nThe price is 300 rupees\n7"] + ["Other text number %d is here\n" % i for i in range(6)]
check("a number in the middle of a page is not a page number", "250" in doc_ocr.strip_repeated_lines(keep)[0][0].split("\n")[1])

layers = {1: ENGLISH + " " + ENGLISH,        # good text, no pictures
          2: MOJIBAKE,                        # garbled
          3: ENGLISH + " " + ENGLISH,        # good text + a big picture
          4: "",                              # empty, no pictures: blank
          5: WATERMARK}                       # watermark only (a picture page)
doc_ocr.pdf_page_count = lambda path: 5
doc_ocr.pdf_text_layer = lambda path, n: layers[n]
doc_ocr.pdf_image_pixels = lambda path: {3: 3_000_000, 5: 3_000_000}
mode.update({"gemini": "ok", "openai": "ok", "tess": "ok ok"})
used_before = up.pages_used_today(base)
g_before = calls["gemini"]
mq = up.create_upload([("mixed.pdf", PDF)], "admin", base)
rq = run(up.process_upload(mq["id"], base, client))
byn = {p["n"]: p for p in rq["pages"]}
check("mixed: good text page is free", byn[1]["engine"] == "pdf-text" and byn[1]["image"] == "")
check("mixed: garbled text page is read from its picture", byn[2]["engine"] == "gemini" and byn[2]["text"] == ASSAMESE and byn[2]["image"] == "p2.jpg" and "old font" in byn[2].get("note", ""))
check("mixed: page with a big picture is read by the AI too", byn[3]["engine"] == "gemini" and byn[3]["image"] == "p3.jpg")
check("mixed: blank page costs no AI page and no picture", byn[4]["engine"] == "pdf-text" and byn[4]["text"] == "" and byn[4]["image"] == "" and not os.path.exists(os.path.join(base, mq["id"], "p4.jpg")))
check("mixed: watermark-only picture page is read as a picture", byn[5]["engine"] == "gemini")
check("mixed: exactly 3 AI pages used (2, 3, 5)", calls["gemini"] - g_before == 3 and up.pages_used_today(base) - used_before == 3)
check("mixed: summary of how pages were read", rq["read_summary"] == {"text_layer": 1, "garbled_layer": 2, "pictures": 1, "blank": 1})
check("mixed: good layer + picture agree = not flagged", byn[3]["review"] is False or "disagree" in byn[3]["reason"])

# the admin can have the whole document read again, every page as a picture
rr = up.reread_upload(mq["id"], "boss", base)
check("reread: back to processing with the flag, edits dropped", rr["status"] == "processing" and rr["force_vision"] and rr["pages"] == [] and rr["reread_by"] == "boss")
check("reread: a document that is still being read cannot be read again", raises(lambda: up.reread_upload(mq["id"], "boss", base), up.UploadError))
g2 = calls["gemini"]
rq2 = run(up.process_upload(mq["id"], base, client))
check("reread: every non-blank page goes to the AI", calls["gemini"] - g2 == 5 and all(p["engine"] == "gemini" for p in rq2["pages"]))

# when the daily limit is used up, a garbled page is read by Tesseract (better than the garbled layer) and flagged
up.daily_limit = lambda: up.pages_used_today(base)
mode["tess"] = ENGLISH
ml = up.create_upload([("limit.pdf", PDF)], "admin", base)
rl = run(up.process_upload(ml["id"], base, client))
p2 = [p for p in rl["pages"] if p["n"] == 2][0]
check("limit: garbled page falls back to Tesseract, flagged", p2["engine"] == "tesseract" and p2["review"] and "limit" in p2["reason"] and "MOJIBAKE" not in p2["text"] and "ø" not in p2["text"])
up.daily_limit = lambda: 500

# a PDF with many pages: repeated header lines are removed
layers = {i: "Running Header SCERT Assam\nUnique content for page number %d about the lesson.\n%d" % (i, i) for i in range(1, 9)}
doc_ocr.pdf_page_count = lambda path: 8
doc_ocr.pdf_image_pixels = lambda path: {}
mh = up.create_upload([("book.pdf", PDF)], "admin", base)
rh = run(up.process_upload(mh["id"], base, client))
check("book: header and page numbers removed, content kept", rh["removed_repeated_lines"] == 16 and all("Running Header" not in p["text"] and ("Unique content for page number %d" % p["n"]) in p["text"] for p in rh["pages"]))

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

# ---- big files: limits and the sent-in-pieces path
MB = 1024 * 1024
check("limits: 150 MB pdf, 20 MB picture", doc_ocr.MAX_PDF_BYTES == 150 * MB and doc_ocr.MAX_IMAGE_BYTES == 20 * MB)


def sparse(path, size, head):
    with open(path, "wb") as f:
        f.write(head)
        f.truncate(size)


big = os.path.join(base, "big.bin")
sparse(big, 150 * MB + 1, PDF)
check("a PDF over 150 MB is refused", raises(lambda: up.create_upload_from_file(big, "big.pdf", "u", base), up.UploadError) and os.path.exists(big))
sparse(big, 150 * MB, PDF)
mb = up.create_upload_from_file(big, "exactly150.pdf", "u", base)
check("a 150 MB PDF is accepted and moved, not copied", mb["kind"] == "pdf" and not os.path.exists(big) and os.path.getsize(os.path.join(base, mb["id"], "orig1.pdf")) == 150 * MB)
sparse(big, 21 * MB, PNG)
check("a picture over 20 MB is refused", raises(lambda: up.create_upload_from_file(big, "photo.png", "u", base), up.UploadError))
sparse(big, 5 * MB, b"MZ-not-a-document")
check("a non-document big file is refused", raises(lambda: up.create_upload_from_file(big, "x.pdf", "u", base), up.UploadError))
os.remove(big)
check("multipart: a PDF over 150 MB is refused", raises(lambda: up.create_upload([("a.pdf", PDF + b"0" * (150 * MB))], "u", base), up.UploadError))

check("begin: over 150 MB refused", raises(lambda: up.begin_chunked("a.pdf", 150 * MB + 1, base), up.UploadError))
check("begin: empty refused", raises(lambda: up.begin_chunked("a.pdf", 0, base), up.UploadError))
data = PDF + os.urandom(20 * MB - len(PDF))
sess = up.begin_chunked("../big book.pdf", len(data), base)
check("begin: 20 MB = 3 pieces of 8 MB", sess["total"] == 3 and sess["chunk_size"] == 8 * MB)
piece = lambda i: data[i * 8 * MB:(i + 1) * 8 * MB]
check("piece out of range", raises(lambda: up.save_chunk(sess["sid"], 3, b"x", base), up.UploadError))
check("piece too big", raises(lambda: up.save_chunk(sess["sid"], 0, b"x" * (8 * MB + 1), base), up.UploadError))
check("unknown session", raises(lambda: up.save_chunk("0123456789abcdef", 0, b"x", base), up.UploadError))
check("bad session id", raises(lambda: up.save_chunk("../../etc", 0, b"x", base), up.UploadError))
check("stored count", up.save_chunk(sess["sid"], 2, piece(2), base) == 1 and up.save_chunk(sess["sid"], 0, piece(0), base) == 2)
check("finish with a missing piece", raises(lambda: up.finish_chunked(sess["sid"], "u", base), up.UploadError))
check("a missing piece keeps the session so the browser can resend it", os.path.isdir(os.path.join(base, "_tmp", sess["sid"])))
up.save_chunk(sess["sid"], 1, piece(1), base)
mc = up.finish_chunked(sess["sid"], "asha", base)
with open(os.path.join(base, mc["id"], "orig1.pdf"), "rb") as f:
    check("pieces are joined in order into the exact file", f.read() == data)
check("session folder is gone after finish", not os.path.exists(os.path.join(base, "_tmp", sess["sid"])))
check("name is cleaned and the upload waits to be read", "/" not in mc["name"] and ".." not in mc["name"] and mc["status"] == "processing" and mc["uploader"] == "asha")
check("temporary folder is not listed as an upload", all(u["id"] != "_tmp" for u in up.list_uploads(base)))

sess2 = up.begin_chunked("short.pdf", 10 * MB, base)
up.save_chunk(sess2["sid"], 0, PDF + b"0" * (8 * MB - len(PDF)), base)
up.save_chunk(sess2["sid"], 1, b"0" * (1 * MB), base)
check("finish with fewer bytes than announced is refused and cleaned", raises(lambda: up.finish_chunked(sess2["sid"], "u", base), up.UploadError) and not os.path.exists(os.path.join(base, "_tmp", sess2["sid"])))

old_sess = up.begin_chunked("old.pdf", 1000, base)
past = os.path.getmtime(os.path.join(base, "_tmp", old_sess["sid"])) - 7 * 3600
os.utime(os.path.join(base, "_tmp", old_sess["sid"]), (past, past))
up.begin_chunked("new.pdf", 1000, base)
check("stale sessions (older than 6 hours) are cleaned up", not os.path.exists(os.path.join(base, "_tmp", old_sess["sid"])))

run(client.aclose())
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
