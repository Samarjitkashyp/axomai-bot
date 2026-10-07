# Axom AI crawler bot: cleaning tools

The crawler bot (`/home/admin/axomai-bot`, https://axomai-bot.aiaxom.co.in/) saves what it crawls as JSON in `crawled_data/`.
These tools remove menus, cards, widgets and duplicate pages from a crawl **without losing real content**.

| File | Where it lives on the server | What it is |
| --- | --- | --- |
| `bot/cleaner.py` | `crawler/cleaner.py` | The cleaning, called from `StorageManager.save_crawl_job` |
| `bot/site_profiles.json` | `crawler/site_profiles.json` | Which sites are cleaned, and how (opt-in per domain) |
| `bot/patch_storage.py` | (run once) | Adds the hook to `crawler/storage.py` (already applied) |
| `bot/test_cleaner.py` | `tests/test_cleaner.py` | Tests: `venv/bin/python3 tests/test_cleaner.py` |
| `clean_report.py` | `tools/clean_report.py` | Dry-run report on a finished crawl: changes nothing |

## How it works
1. A crawl finishes. If the site has an enabled profile, the crawl **as crawled** is first archived to `crawled_data_raw/`.
2. The pages are cleaned (copy only). Both the ChromaDB index and the import into Axom AI read the cleaned file.
3. Any problem (error, a must-keep phrase would be lost, more than 60% of the words would go) leaves the pages untouched.
A site **without** a profile is saved exactly as before.

## Rules (a line is removed only if all hold)
- it is on at least `threshold` (default 30%) of the pages, and on at least 3, and the crawl has at least 10 pages;
- it is not the page title, not in the first 6 lines of its page (3+ words), not a "Day 1:" label, not a paragraph of 40+ words,
  and has no price, phone number or e-mail address;
- the page is not a listing/hub page (`keep_pages`).
Pages are dropped only when they are an exact copy of an earlier page or match a `junk_urls` pattern.

## Adding a site
1. Crawl it, then run the report on the raw JSON:
   `python3 tools/clean_report.py crawled_data_raw/<job>.json --out reports --terms <file> --keep-pages '<regex>' ...`
2. Read `reports/<job>_report.md` and `_samples.md`: flagged pages, lines removed, lines that exist only on dropped pages.
3. Add an entry to `crawler/site_profiles.json` (`enabled`, `junk_urls`, `keep_pages`, `terms`) and restart: `sudo systemctl restart axomai-bot`.

## Rollback
`cd /home/admin/axomai-bot && rm -f crawler/cleaner.py crawler/site_profiles.json && tar xzf /home/admin/axom_backups/axomai-bot-code-<stamp>.tgz && sudo systemctl restart axomai-bot`
(to switch one site off, set `"enabled": false` for it in `site_profiles.json`; no restart needed, the file is read on every save).

## The crawl-form options (checked with `bot/test_crawl_options.py`, against a local test server)
- **Render JavaScript** loads each page in headless Chromium (a new browser per page: slow, 300-600 MB while it runs). Works; only needed when a site's text is made by JavaScript.
- **Force Re-crawl (Bypass Smart Skip)**: Smart Skip only *labels and counts* unchanged pages (`skipped_unchanged`); every page is still downloaded, saved and imported. Force Re-crawl turns the labelling off (an unchanged page then counts as "new"). It does not change which pages are saved.
- **Include Subdomains** also crawls sibling sub-sites. It used to take the last two labels of a host as "the domain", so under `gov.in`, `co.in` or `co.uk` every site of the whole suffix counted as the same site. Fixed: those suffixes use three labels (`assam.gov.in`).
- A site profile with `"allow_subdomains": false` switches Include Subdomains off for that site even if it is ticked (news18 has it).

## Import into Axom AI
`app/chat_import.py` sends the pages in batches of 200, retries a failed batch three times and stops at once if the token is wrong. Before, a whole crawl went in one request and a big one could be lost.

## Profiles in use
`myvoyage.co.in` (junk URLs, hub pages, must-keep places) and `assam.news18.com` (only that host, no tag/pagination pages, `top_lines: 0` because the top of a news page is the menu and the trending ticker). Lines repeated word for word on one page are kept once.
