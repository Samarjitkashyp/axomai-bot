# -*- coding: utf-8 -*-
"""Text from uploaded documents (images and PDFs, any language) for the Axom AI chat.

Order of engines for every page that has to be read from a picture:
    1. Google Gemini (vision)       - reads Assamese, Hindi, Bengali, English, handwriting and rough phone scans well;
    2. OpenAI vision                - used when Gemini is not available (no key, quota used up, error);
    3. Tesseract (on the server)    - free and offline; weaker on Assamese, so its result is always flagged "review".
A PDF that already has a text layer is read with pdftotext (exact, free); only the pages without text are read as pictures.

Every page comes back as {"text", "engine", "review", "reason"}. "review" is a hint for the admin who approves the text:
the page was read by Tesseract only, is nearly empty, or the engine's text and Tesseract's text disagree a lot.
Nothing here talks to Axom AI: the admin approves first (app/uploads.py).
"""
import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger("axomai.ocr")

MAX_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("UPLOAD_MAX_PDF_PAGES", "200"))
TEXT_LAYER_MIN_CHARS = 40       # a PDF page with fewer characters than this is treated as a picture
TESS_LANGS = os.getenv("TESSERACT_LANGS", "asm+hin+eng")
GEMINI_VISION_MODELS = [m for m in os.getenv("GEMINI_VISION_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m]
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")
KEY_FILE = os.getenv("QA_ENV_FILE", "/home/admin/axom_ai/.env")
AGREE_MIN = 0.45                # word overlap with Tesseract under this -> "review"
MIN_WORDS = 3

PROMPT = (
    "Transcribe ALL the text in this image exactly as written, in its original language and script "
    "(Assamese, Bengali, Hindi, English or any other). Do not translate, summarize, correct or explain. "
    "Keep the reading order and paragraphs; write a table as lines with ' | ' between the cells. "
    "Skip page numbers, watermarks and decorative marks. If the image has no readable text, return an empty string. "
    "Return only the text."
)


class EngineUnavailable(Exception):
    """This engine cannot be used right now (no key, quota used up, not installed)."""


# ---------------------------------------------------------------- file types
def detect_kind(data: bytes) -> Optional[str]:
    """"pdf" / "png" / "jpeg" / "webp" from the first bytes (the file name is not trusted), else None."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
EXT = {"png": "png", "jpeg": "jpg", "webp": "webp"}


def _env_key(name: str) -> str:
    key = os.environ.get(name, "")
    if key:
        return key
    try:
        from dotenv import dotenv_values
        return (dotenv_values(KEY_FILE).get(name) or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- engines
async def gemini_read(data: bytes, mime: str, client: httpx.AsyncClient, key: Optional[str] = None) -> str:
    key = key if key is not None else _env_key("GEMINI_API_KEY")
    if not key:
        raise EngineUnavailable("no GEMINI_API_KEY")
    body = {"contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}]}],
            "generationConfig": {"temperature": 0}}
    last = "no model"
    for model in GEMINI_VISION_MODELS:
        for attempt in range(2):
            try:
                r = await client.post("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                                      json=body, headers={"x-goog-api-key": key}, timeout=120)
            except Exception as e:
                last = type(e).__name__
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    return "".join(p.get("text", "") for p in parts).strip()
                except Exception:
                    last = "unreadable answer"      # e.g. blocked: try the next model
                    break
            last = "HTTP %d" % r.status_code
            if r.status_code in (500, 502, 503, 504):
                await asyncio.sleep(2 * (attempt + 1))
                continue
            break                                    # 404 / 429 / 400 / 403: next model
    raise EngineUnavailable("gemini: " + last)


async def openai_read(data: bytes, mime: str, client: httpx.AsyncClient, key: Optional[str] = None) -> str:
    key = key if key is not None else _env_key("OPENAI_API_KEY")
    if not key:
        raise EngineUnavailable("no OPENAI_API_KEY")
    body = {"model": OPENAI_VISION_MODEL, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())}}]}]}
    last = "error"
    for attempt in range(2):
        try:
            r = await client.post("https://api.openai.com/v1/chat/completions", json=body,
                                  headers={"Authorization": "Bearer " + key}, timeout=120)
        except Exception as e:
            last = type(e).__name__
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return (r.json()["choices"][0]["message"]["content"] or "").strip()
            except Exception:
                last = "unreadable answer"
                break
        last = "HTTP %d" % r.status_code
        if r.status_code in (500, 502, 503, 504):
            await asyncio.sleep(2 * (attempt + 1))
            continue
        break
    raise EngineUnavailable("openai: " + last)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def tesseract_read(path: str) -> str:
    if not tesseract_available():
        raise EngineUnavailable("tesseract is not installed")
    langs = TESS_LANGS
    try:
        have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
        langs = "+".join(l for l in TESS_LANGS.split("+") if l in have) or "eng"
    except Exception:
        pass
    p = subprocess.run(["tesseract", path, "stdout", "-l", langs, "--psm", "3"], capture_output=True, timeout=180)
    if p.returncode != 0:
        raise EngineUnavailable("tesseract failed")
    return p.stdout.decode("utf-8", "replace").strip()


# ---------------------------------------------------------------- comparing and cleaning
def clean_text(text: str) -> str:
    """Control characters out, spaces and blank lines tidied; the words themselves are not touched."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​﻿]", "", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _words(text: str) -> set:
    return set(re.findall(r"\w+", (text or "").lower()))


def agreement(a: str, b: str) -> float:
    """Share of the shorter text's words that the other text also has (1 = same words, 0 = nothing in common)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def judge(text: str, engine: str, cross_check: Optional[str] = None) -> Tuple[bool, str]:
    """(review?, why) - a hint for the admin, never a reason to drop text."""
    if len(_words(text)) < MIN_WORDS:
        return True, "very little text"
    if engine == "tesseract":
        return True, "read by Tesseract only (weaker on Assamese)"
    if cross_check and len(_words(cross_check)) >= MIN_WORDS and agreement(text, cross_check) < AGREE_MIN:
        return True, "the two readers disagree"
    return False, ""


# ---------------------------------------------------------------- one picture
async def read_image(path: str, kind: str, client: httpx.AsyncClient, paid_ok: bool = True) -> Dict[str, Any]:
    """Text of one image file. paid_ok=False (daily limit reached) skips Gemini / OpenAI and uses Tesseract."""
    mime = MIME[kind]
    with open(path, "rb") as f:
        data = f.read()
    tess = ""
    try:
        tess = await asyncio.to_thread(tesseract_read, path)
    except EngineUnavailable:
        pass
    text, engine, notes = "", "", []
    if paid_ok:
        for name, fn in (("gemini", gemini_read), ("openai", openai_read)):
            try:
                text = await fn(data, mime, client)
                engine = name
                break
            except EngineUnavailable as e:
                notes.append(str(e))
    if not engine:
        text, engine = tess, "tesseract"
        if not tess:
            notes.append("tesseract: no text")
    text = clean_text(text)
    review, why = judge(text, engine, tess if engine != "tesseract" else None)
    if not paid_ok:
        review, why = True, "daily page limit reached: read by Tesseract only"
    return {"text": text, "engine": engine, "review": review, "reason": why, "notes": notes}


# ---------------------------------------------------------------- PDFs
def poppler_available() -> bool:
    return shutil.which("pdftotext") is not None and shutil.which("pdftoppm") is not None


def pdf_page_count(path: str) -> int:
    if shutil.which("pdfinfo") is None:
        raise EngineUnavailable("poppler-utils is not installed")
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=30).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def pdf_text_layer(path: str, page: int) -> str:
    p = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=60)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""


def pdf_render(path: str, page: int, out_base: str, dpi: int = 150) -> str:
    """Renders one page as a JPEG; returns its path."""
    p = subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-jpeg", "-singlefile", path, out_base], capture_output=True, timeout=120)
    if p.returncode != 0 or not os.path.exists(out_base + ".jpg"):
        raise EngineUnavailable("could not draw PDF page %d" % page)
    return out_base + ".jpg"
