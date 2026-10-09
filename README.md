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
## Tools (`tools/`)
- `run_crawl.py` - run one crawl from the command line with the bot's engine (no dashboard login); does not import into Axom AI
- `site_dryrun.py` - dry-run a candidate site profile on saved crawls (changes nothing)
- `refetch_blocked.py` - slowly re-fetch pages that were saved as block pages
- `blogger_feed.py` - full post text of a Blogger blog from its feed (when the pages are rate-limited)
- `clean_report.py` - cleaning report

How the cleaner and profiles work: [docs/CLEANING.md](docs/CLEANING.md).

## Passages + AI questions for the chat (`app/qa_chunks.py`)
After every crawl (dashboard or auto-crawl) the bot cuts each page into passages of about 100-230 words and asks Gemini for one
question per passage, in the passage's own language. Each passage is stored in Axom AI as its own question + answer pair
(url `page#c1`, `#c2` ...), so the chat finds the passage that answers a visitor's question instead of one whole page.
It works on any crawl (plain HTML, JavaScript-rendered, WordPress API, Next.js), because it only uses the crawled text.
- Falls back to the old whole-page import when the AI is unavailable (no key, quota, model gone); a passage without a usable question gets a plain fallback question.
- The rows it replaces are saved first to `/home/admin/axom_backups/qa-replaced-<site>-<time>.json`.
- Pages that did not change and already have passages are skipped (no new AI calls). At most `QA_MAX_PASSAGES` (1500) passages per crawl.
- The key: `GEMINI_API_KEY` in the bot's environment, else read from `QA_ENV_FILE` (default `/home/admin/axom_ai/.env`). Model: `QA_MODEL` (default `gemini-3.5-flash-lite`); if it is gone or over quota the other flash models of the account are tried.
- Off for everything: `AXOMAI_QA_CHUNKS=0`. Off for one site: `"qa_chunks": false` in its profile. Preview on a saved crawl: `tools/make_qa.py`.
