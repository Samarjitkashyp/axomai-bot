# Passages and AI questions for the chat (`app/qa_chunks.py`)
After every crawl (dashboard or auto-crawl) the bot cuts each page into passages of about 100-230 words and asks Gemini for one
question per passage, in the passage's own language. Each passage is stored in Axom AI as its own question + answer pair
(url `page#c1`, `#c2` ...), so the chat finds the passage that answers a visitor's question instead of one whole page.
It works on any crawl (plain HTML, JavaScript-rendered, WordPress API, Next.js), because it only uses the crawled text.
- Falls back to the old whole-page import when the AI is unavailable (no key, quota, model gone); a passage without a usable question gets a plain fallback question.
- The rows it replaces are saved first to `/home/admin/axom_backups/qa-replaced-<site>-<time>.json`.
- Pages that did not change and already have passages are skipped (no new AI calls). At most `QA_MAX_PASSAGES` (1500) passages per crawl.
- The key: `GEMINI_API_KEY` in the bot's environment, else read from `QA_ENV_FILE` (default `/home/admin/axom_ai/.env`). Model: `QA_MODEL` (default `gemini-3.5-flash-lite`); if it is gone or over quota the other flash models of the account are tried.
- Off for everything: `AXOMAI_QA_CHUNKS=0`. Off for one site: `"qa_chunks": false` in its profile. Preview on a saved crawl: `tools/make_qa.py`.
