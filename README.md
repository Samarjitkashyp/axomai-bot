# axomai-bot

Web crawler and knowledge bot for Axom AI (FastAPI + uvicorn). It crawls sites, optionally cleans the pages per site,
stores them, builds a ChromaDB index and sends the pages to Axom AI (`/api/import-crawl/`).

- `app/` - FastAPI app, login (Django `auth_user` + `bot_access`), scheduler, batch import (`chat_import.py`)
- `crawler/` - engine, frontier, extractor, catalog (smart skip), storage, `cleaner.py` and `site_profiles.json` (opt-in per-site cleaning)
- `rag/` - chunker, vector store, LLM client
- `static/` - web UI
- `tests/` - cleaner, import and crawl-option tests (`python3 tests/test_cleaner.py`)
- `tools/` - dry-run report helpers

## Run
```
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 2
```
Not in git on purpose: `.env`, `venv/`, `crawled_data/`, `crawled_data_raw/`, `vector_store/`, `reports/`.

Site cleaning: a site is cleaned only if it has an enabled profile in `crawler/site_profiles.json`. Safety gates fall back to
the raw pages, and raw crawls are archived in `crawled_data_raw/`. Block pages (CAPTCHA / Access Denied) are never imported.
Extra tools and docs: https://github.com/Samarjitkashyp/axomai-browser/tree/main/server/axomai-bot-tools
