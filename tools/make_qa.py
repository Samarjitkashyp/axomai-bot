# -*- coding: utf-8 -*-
"""Turns the pages of a crawl into small question + answer pairs for the Axom AI chat.

For each page the text is cut into passages of about 100-230 words (paragraph boundaries), and an AI model (Gemini) writes ONE
natural question for each passage, in the passage's own language. Chat matches the visitor's question against these questions,
so "what is the phone number?" can now find the passage that has the phone number, and the answer is only that passage.

The output has the same shape as a crawl ("pages" with url, title = the question, content = the answer), so it goes in through
the normal import (chat_import.push_pages); the url is the page url + "#c1", "#c2" ... It changes nothing in Axom AI by itself.

    python3 make_qa.py --crawl crawled_data/job_x_site.json --out /tmp/qa.json [--env-file /home/admin/axom_ai/.env] [--limit-pages 0]
                       [--brand "Digihive Assam"] [--model gemini-3.5-flash-lite]

The API key is read from the GEMINI_API_KEY environment variable (or from --env-file); it is never printed.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import httpx

BOT = os.environ.get("BOT_DIR", "/home/admin/axomai-bot")
sys.path.insert(0, BOT)
from app.export import common_suffix, page_name, page_text  # noqa: E402  (the same cleaning as the Excel / PDF / JSON exports)

TARGET_WORDS = 160      # a passage is closed once it has this many words
MAX_WORDS = 230         # ... or when the next paragraph would take it over this
MIN_LAST_WORDS = 40     # a shorter last passage is joined to the one before it
TINY_WORDS = 12         # a passage with fewer words is not worth a row
BATCH = 8
Q_MIN, Q_MAX = 8, 220   # characters


def split_chunks(text: str) -> List[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[List[str]] = []
    cur: List[str] = []
    n = 0
    for p in paras:
        w = len(p.split())
        if cur and n + w > MAX_WORDS:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += w
        if n >= TARGET_WORDS:
            chunks.append(cur)
            cur, n = [], 0
    if cur:
        chunks.append(cur)
    if len(chunks) > 1 and sum(len(x.split()) for x in chunks[-1]) < MIN_LAST_WORDS:
        last = chunks.pop()
        chunks[-1] = chunks[-1] + last
    out = ["\n\n".join(c) for c in chunks]
    return [c for c in out if len(c.split()) >= TINY_WORDS]


PROMPT = """You write search questions for a website's knowledge base.
Below are text passages from the website "{site}" (page: "{name}"). For EACH passage write ONE natural question that a visitor could ask and that the passage answers directly.
Rules:
- Write the question in the same language and script as its passage (Assamese stays Assamese, Hindi stays Hindi, English stays English).
- Be specific: name the service, product, place, person or topic the passage is about. Mention "{brand}" when the passage is about the business itself (contact details, about, prices, services).
- Do not add any fact that is not in the passage. 6 to 20 words. End with a question mark.
Return only JSON, one entry per passage: [{{"id": <id>, "question": "<question>"}}]

Passages:
{passages}
"""


async def ask_gemini(client: httpx.AsyncClient, model: str, key: str, prompt: str) -> Optional[List[Dict[str, Any]]]:
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for attempt in range(3):
        try:
            r = await client.post(url, json=body, headers={"x-goog-api-key": key}, timeout=90)
            if r.status_code == 200:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                return data if isinstance(data, list) else None
            if r.status_code not in (429, 500, 502, 503, 504):
                raise SystemExit("Gemini error %d (not retried): %s" % (r.status_code, r.text[:300].replace(key, "***")))
        except Exception as e:
            print("  Gemini call failed (%s)" % type(e).__name__)
        await asyncio.sleep(3 * (attempt + 1))
    return None


def valid_question(q: Any) -> bool:
    if not isinstance(q, str):
        return False
    q = q.strip()
    return Q_MIN <= len(q) <= Q_MAX and ("?" in q or "？" in q)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env-file", default="")
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--brand", default="")
    ap.add_argument("--limit-pages", type=int, default=0)
    ap.add_argument("--show", type=int, default=12, help="how many pairs to print as a sample")
    a = ap.parse_args()
    if a.env_file:
        from dotenv import load_dotenv
        load_dotenv(a.env_file)
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        sys.exit("GEMINI_API_KEY is not set")

    crawl = json.load(open(a.crawl, encoding="utf-8"))
    pages = crawl["pages"][: a.limit_pages or None]
    seed = crawl.get("seed_url", "")
    site = re.sub(r"^www\.", "", re.sub(r"^https?://", "", seed).split("/")[0])
    brand = a.brand or site.split(".")[0].title()

    names = [page_name(p) for p in pages]
    tail = common_suffix(names)
    jobs = []   # (page, short name, chunks)
    for p, name in zip(pages, names):
        short = name[: -len(tail)].strip() if tail and name.endswith(tail) and len(name) > len(tail) else name
        text = page_text(p.get("content") or "", {name, short})
        chunks = split_chunks(text) if text else []
        if chunks:
            jobs.append((p, short, chunks))
    total = sum(len(c) for _, _, c in jobs)
    print("%s: %d pages -> %d passages, asking %s for the questions (%d per call)" % (site, len(jobs), total, a.model, BATCH))

    out_pages, failed = [], 0
    async with httpx.AsyncClient() as client:
        for p, short, chunks in jobs:
            for start in range(0, len(chunks), BATCH):
                part = chunks[start:start + BATCH]
                listing = "\n\n".join("[id %d]\n%s" % (i, c) for i, c in enumerate(part))
                res = await ask_gemini(client, a.model, key, PROMPT.format(site=site, name=short, brand=brand, passages=listing))
                qmap = {}
                if res:
                    for e in res:
                        if isinstance(e, dict) and isinstance(e.get("id"), int) and valid_question(e.get("question")):
                            qmap[e["id"]] = e["question"].strip()
                for i, c in enumerate(part):
                    q = qmap.get(i)
                    if not q:
                        failed += 1
                        continue
                    n = start + i + 1
                    out_pages.append({"url": "%s#c%d" % (p["url"].split("#")[0], n), "title": q, "content": "%s\n\n%s" % (short, c)})
                await asyncio.sleep(0.5)
    json.dump({"seed_url": seed, "job_id": "job_qa_" + site.split(".")[0], "pages": out_pages}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("pairs written: %d | passages without a usable question (left out): %d | file: %s" % (len(out_pages), failed, a.out))
    step = max(1, len(out_pages) // max(1, a.show))
    for it in out_pages[::step][: a.show]:
        print("\nQ: %s\nA: (%d words) %s" % (it["title"], len(it["content"].split()), it["content"][:170].replace("\n", " | ")))


if __name__ == "__main__":
    asyncio.run(main())
