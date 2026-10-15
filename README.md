# axomai-bot

**Crawler and document-ingestion service for [Axom AI](https://aiaxom.co.in).** It crawls websites, reads uploaded photos and PDFs, turns the text into question-and-answer passages and sends them to the Axom AI knowledge base after review.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-vector%20index-FF6B6B)
![License](https://img.shields.io/badge/License-Proprietary-lightgrey)

Console: https://axomai-bot.aiaxom.co.in (sign in with an Axom AI admin account that has bot access).

---

## What it does

| Capability | Summary |
|---|---|
| **Crawling** | Static and JavaScript-rendered pages (Playwright), WordPress and Next.js data sources, and auto-crawl on a schedule. |
| **Cleaning** | Opt-in, per-site cleaning profiles with safety gates. Raw crawls are archived and block pages (CAPTCHA, Access Denied) are never imported. |
| **Documents** | Photos and PDFs read with Gemini or OpenAI vision, with Tesseract as the fallback. Old-font PDFs are read as pictures. An admin reviews and approves before anything reaches the chat. |
| **Passages** | Each page is cut into passages of about 100–230 words; Gemini writes one question per passage so the chat can find the exact answer. |
| **Index** | ChromaDB vector index with multilingual sentence embeddings for search and a RAG test chat. |
| **Import** | Batched, retrying import into Axom AI (`/api/import-crawl/`) with a backup of every row it replaces. |
| **Export** | Crawl results as CSV, JSON or PDF. |

## How it fits

```
 Websites / uploads ──▶  axomai-bot  ──▶  Axom AI (Django)  ──▶  chat answers
                        crawl · clean      import API             grounded in
                        read · passages    admin review           the knowledge base
                        vector index
```

Sign-in uses Axom AI's own user table: superusers, or users with the *bot* permission. Documents are approved in the Axom AI admin (**Document Verify**).

## Repository layout

```
app/        FastAPI app: API, sign-in, scheduler, import, uploads, OCR, passages
crawler/    Engine, frontier, extractor, cleaner, site profiles, storage
rag/        Chunker, vector store, LLM client
static/     Web console
tools/      Command-line helpers (dry runs, reports, re-fetch)
tests/      Test scripts
docs/       Cleaning, uploads and passages
```

## Getting started

**Prerequisites:** Python 3.11+, access to the Axom AI PostgreSQL database, and for documents `tesseract-ocr` (`asm`, `hin`, `eng`) and `poppler-utils`.

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
cp .env.example .env            # fill in the values
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 2
```

Run one crawl from the command line, without signing in or importing: `venv/bin/python tools/run_crawl.py`.

## Configuration

Settings are environment variables; copy [`.env.example`](.env.example) to `.env` and never commit it.

| Group | Variables |
|---|---|
| Core | `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` |
| Import into Axom AI | `CHAT_IMPORT_URL`, `BOT_IMPORT_TOKEN` |
| AI providers | `GEMINI_API_KEY`, `OPENAI_VISION_MODEL`, `GEMINI_VISION_MODELS` |
| Passages | `QA_MODEL`, `QA_MAX_PASSAGES`, `AXOMAI_QA_CHUNKS` (`0` turns them off) |
| Documents | `UPLOAD_DIR`, `UPLOAD_MAX_PDF_PAGES`, `TESSERACT_LANGS` |
| Housekeeping | `BACKUP_DIR` |

Not in git on purpose: `.env`, `venv/`, `crawled_data/`, `crawled_data_raw/`, `vector_store/`, `reports/`, `uploads/`.

## API overview

All routes except `/login`, `/logout` and `/health` need a signed-in session.

| Area | Routes |
|---|---|
| Crawl | `POST /api/crawl/start`, `GET /api/crawl/status/{id}`, `GET /api/crawl/jobs`, `POST /api/crawl/stop/{id}`, exports under `/api/crawl/export/{id}/{csv,json,pdf}` |
| Documents | `POST /api/upload` (and chunked `begin` / `chunk` / `finish`), `GET /api/upload/list`, review `PUT .../page/{n}`, `POST .../{approve,reread,reject,resend}` |
| Index | `POST /api/rag/index/{id}`, `POST /api/rag/query`, `POST /api/rag/chat`, `GET /api/rag/stats` |
| Schedule | `GET /api/schedule/list`, `POST /api/schedule/toggle` |

## Testing

Each test file is a standalone script:

```bash
venv/bin/python tests/test_cleaner.py
venv/bin/python tests/test_login.py       # sign-in rules, lockout and page (the flow part needs the bot's packages)
```

The others (`test_uploads`, `test_chat_import`, `test_qa_chunks`, `test_cleanup`, `test_export`, `test_sources`, `test_crawl_options`, `test_next_data`) run the same way.

## Security

- Sign-in follows the Axom AI admin rules: 3 wrong tries lock the IP and username for 24 hours (shared with the admin console), every attempt is audit-logged, forms carry a CSRF token and pages are not cached.
- Uploads are limited by type, size and page count, and are never public.
- Block pages and empty pages are never imported; rows replaced during an import are saved to a backup first.
- Secrets are read from the environment only.

## Deployment

Runs as a systemd service (`uvicorn`, 2 workers) on `127.0.0.1:8001` behind nginx and Cloudflare. Before updating, back up `app/`, `static/` and `crawler/site_profiles.json`, copy the new files, run the relevant tests and restart the service.

## Documentation

- [Cleaning and site profiles](docs/CLEANING.md)
- [Uploads and document reading](docs/UPLOADS.md)
- [Passages and AI questions](docs/QA_PASSAGES.md)

Command-line tools in `tools/`: `run_crawl.py`, `site_dryrun.py`, `refetch_blocked.py`, `blogger_feed.py`, `clean_report.py`, `make_qa.py`.

## License

Proprietary software. All rights reserved. © 2026 Axom AI.

Maintainer: Samarjit Kashyap, samarjitkashyp@gmail.com
