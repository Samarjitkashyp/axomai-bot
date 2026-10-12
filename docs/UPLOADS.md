# Upload & Scan Documents

Photos and PDFs (any language) -> text -> **admin approval** -> Axom AI chat. Section "Upload & Scan Documents" on the bot dashboard, above "Crawled Datasets".

* **Who:** anyone who can log in to the bot panel. Nothing is public.
* **Files:** PNG / JPG / WEBP or one PDF (a picture up to 20 MB, a PDF up to 150 MB and 600 pages; big files are sent in 8 MB pieces because Cloudflare refuses one request over 100 MB); a phone scan = several pictures = one document. The real file type is checked, not the name.
* **Reading order for a picture:** Gemini vision -> OpenAI vision (when Gemini has no key / quota) -> Tesseract (asm+hin+eng, always flagged "review"). A PDF page that already has text is read with `pdftotext` (exact, free).
* **Daily limit:** 500 pages a day go to Gemini/OpenAI (admin setting `bot_upload_daily_pages` in SystemSetting; default 500). After that pages are read by Tesseract only and flagged.
* **Review:** the admin sees the page picture next to its text, corrects it, then approves. Only then is the document saved like a crawl (`doc-<id>`, shown in "Crawled Datasets") and sent to the chat with the same passages + questions step as a crawled site. Chat sources for uploads point to `https://documents.aiaxom.co.in/<id>#pN` (not a real page).
* **Files on the server:** `uploads/<id>/` (originals, page pictures, `meta.json`). Chat import keeps the usual 50-character minimum per page.
* **Server needs:** `tesseract-ocr` (+ `-asm -hin -eng`), `poppler-utils`, and `GEMINI_API_KEY` / `OPENAI_API_KEY` in `/home/admin/axom_ai/.env` (or the bot's own env). `OPENAI_VISION_MODEL` (default `gpt-4o`) can be changed by environment variable.
* Tests: `python3 tests/test_uploads.py`.

## PDFs: which pages are read how
Per page of a PDF: the text layer is used only when it is **readable** (`doc_ocr.layer_quality`) and the page has no big pictures. Otherwise the page is drawn and read as a picture (Gemini, then OpenAI, then Tesseract):
* **old-font PDFs** (Assamese / Hindi textbooks whose text copies out as symbols like `ø¬ıÚ±˜”˘œ˚˛±`) and pages that only hold a rotated watermark: read from the picture of the page;
* **pages with pictures** (total picture area over 150,000 pixels): read as a picture too, so text inside pictures is not lost; a good text layer is then only the cross-check;
* **blank pages** (no text, no pictures): Tesseract looks first, so no AI page is spent;
* repeated running headers and bare page numbers are removed from long documents (the count is shown to the admin);
* the admin can press **Re-read with AI vision** (Document Verify) to read the whole document again, every page as a picture.
