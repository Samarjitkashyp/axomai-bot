# Upload & Scan Documents

Photos and PDFs (any language) -> text -> **admin approval** -> Axom AI chat. Section "Upload & Scan Documents" on the bot dashboard, above "Crawled Datasets".

* **Who:** anyone who can log in to the bot panel. Nothing is public.
* **Files:** PNG / JPG / WEBP or one PDF (20 MB each, up to 200 PDF pages); a phone scan = several pictures = one document. The real file type is checked, not the name.
* **Reading order for a picture:** Gemini vision -> OpenAI vision (when Gemini has no key / quota) -> Tesseract (asm+hin+eng, always flagged "review"). A PDF page that already has text is read with `pdftotext` (exact, free).
* **Daily limit:** 500 pages a day go to Gemini/OpenAI (admin setting `bot_upload_daily_pages` in SystemSetting; default 500). After that pages are read by Tesseract only and flagged.
* **Review:** the admin sees the page picture next to its text, corrects it, then approves. Only then is the document saved like a crawl (`doc-<id>`, shown in "Crawled Datasets") and sent to the chat with the same passages + questions step as a crawled site. Chat sources for uploads point to `https://documents.aiaxom.co.in/<id>#pN` (not a real page).
* **Files on the server:** `uploads/<id>/` (originals, page pictures, `meta.json`). Chat import keeps the usual 50-character minimum per page.
* **Server needs:** `tesseract-ocr` (+ `-asm -hin -eng`), `poppler-utils`, and `GEMINI_API_KEY` / `OPENAI_API_KEY` in `/home/admin/axom_ai/.env` (or the bot's own env). `OPENAI_VISION_MODEL` (default `gpt-4o`) can be changed by environment variable.
* Tests: `python3 tests/test_uploads.py`.
