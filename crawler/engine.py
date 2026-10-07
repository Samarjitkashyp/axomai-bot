import asyncio
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
import httpx

from crawler.frontier import URLFrontier
from crawler.extractor import ContentExtractor
from crawler.storage import StorageManager
from crawler.catalog import URLCatalog


class CrawlerJob:
    """
    State tracker for an active or completed crawl task.
    """
    def __init__(
        self,
        job_id: str,
        seed_url: str,
        max_pages: int,
        max_depth: int,
        crawl_delay: float,
        render_js: bool,
        force_recrawl: bool = False,
        include_subdomains: bool = False
    ):
        self.job_id = job_id
        self.seed_url = seed_url
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.crawl_delay = crawl_delay
        self.render_js = render_js
        self.force_recrawl = force_recrawl
        self.include_subdomains = include_subdomains

        self.status = "queued"  # queued, running, stopping, stopped, completed, failed
        self.is_cancelled = False
        self.pages_crawled = 0
        self.pages_new = 0
        self.pages_updated = 0
        self.pages_skipped = 0

        self.current_url: Optional[str] = None
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.duration_seconds: float = 0.0
        self.saved_file_path: Optional[str] = None
        self.errors: List[str] = []
        self.logs: List[str] = []
        self.pages: List[Dict[str, Any]] = []

    def cancel(self):
        """Signals the crawler to stop gracefully."""
        self.is_cancelled = True
        self.status = "stopping"
        self.log("🛑 Cancellation requested. Wrapping up visited pages...")

    def log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.logs.append(entry)
        # Keep latest 200 logs
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "seed_url": self.seed_url,
            "status": self.status,
            "pages_crawled": self.pages_crawled,
            "pages_new": self.pages_new,
            "pages_updated": self.pages_updated,
            "pages_skipped": self.pages_skipped,
            "force_recrawl": self.force_recrawl,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "render_js": self.render_js,
            "current_url": self.current_url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "saved_file_path": self.saved_file_path,
            "error_count": len(self.errors),
            "latest_logs": self.logs[-10:],
            "latest_pages": [
                {"url": p["url"], "title": p["title"], "word_count": p["word_count"], "change_status": p.get("change_status", "new")}
                for p in self.pages[-5:]
            ]
        }


class CrawlerEngine:
    """
    Executes asynchronous crawling for a given job.
    Supports both fast HTTPX fetching and headless Playwright rendering.
    """

    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    def __init__(
        self,
        job: CrawlerJob,
        storage_manager: Optional[StorageManager] = None,
        catalog: Optional[URLCatalog] = None
    ):
        self.job = job
        # AXOMAI-LOCK: a site whose profile (crawler/site_profiles.json) says allow_subdomains=false is never crawled across subdomains
        try:
            from crawler.cleaner import load_profiles
            from urllib.parse import urlparse as _urlparse
            _host = _urlparse(job.seed_url).netloc.lower().replace("www.", "")
            _prof = load_profiles().get(_host)
            if job.include_subdomains and isinstance(_prof, dict) and _prof.get("allow_subdomains") is False:
                job.include_subdomains = False
                job.log("Include Subdomains was switched off: the profile of %s does not allow it" % _host)
        except Exception:
            pass
        self.storage = storage_manager or StorageManager()
        self.catalog = catalog or URLCatalog()
        self.frontier = URLFrontier(
            seed_url=job.seed_url,
            max_pages=job.max_pages,
            max_depth=job.max_depth,
            include_subdomains=job.include_subdomains
        )

    async def fetch_static(self, client: httpx.AsyncClient, url: str) -> Optional[tuple[str, int]]:
        """
        Fetches static HTML via HTTPX with UTF-8 fallback.
        """
        try:
            response = await client.get(url, follow_redirects=True, timeout=15.0)
            # UTF-8 decoding priority
            response.encoding = response.encoding or "utf-8"
            return response.text, response.status_code
        except httpx.HTTPStatusError as e:
            self.job.log(f"HTTP error for {url}: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            self.job.log(f"Request failed for {url}: {str(e)}")
            return None
        except Exception as e:
            self.job.log(f"Error fetching {url}: {str(e)}")
            return None

    async def fetch_dynamic(self, playwright, url: str) -> Optional[tuple[str, int]]:
        """
        Renders dynamic JavaScript pages via Playwright headless Chromium.
        """
        browser = None
        try:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.DEFAULT_USER_AGENT)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            # Short wait for any client-side JavaScript hydration
            await asyncio.sleep(1.0)
            content = await page.content()
            status_code = response.status if response else 200
            await browser.close()
            return content, status_code
        except Exception as e:
            self.job.log(f"Playwright error on {url}: {str(e)}")
            if browser:
                await browser.close()
            return None

    async def run(self):
        """
        Main crawling loop running as an asynchronous background task.
        """
        self.job.status = "running"
        self.job.started_at = datetime.now().isoformat()
        start_time = time.time()
        self.job.log(f"Started crawl for: {self.job.seed_url} (JS Render: {self.job.render_js})")

        headers = {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,as;q=0.8,hi;q=0.7",
        }

        playwright_instance = None
        if self.job.render_js:
            try:
                from playwright.async_api import async_playwright
                playwright_context = async_playwright()
                playwright_instance = await playwright_context.__aenter__()
            except Exception as e:
                self.job.log(f"Playwright initialization failed, falling back to static: {e}")
                self.job.render_js = False

        try:
            async with httpx.AsyncClient(headers=headers, verify=False) as client:
                while self.frontier.has_more():
                    if self.job.is_cancelled:
                        self.job.log(f"🛑 [Crawl Stopped]: Stopped by user request. Saving {self.job.pages_crawled} crawled pages.")
                        break

                    item = self.frontier.get_next()
                    if not item:
                        break

                    current_url, depth = item
                    self.job.current_url = current_url
                    self.job.log(f"Visiting [Depth {depth}]: {current_url}")

                    # Fetch page content
                    fetch_result = None
                    if self.job.render_js and playwright_instance:
                        fetch_result = await self.fetch_dynamic(playwright_instance, current_url)
                    else:
                        fetch_result = await self.fetch_static(client, current_url)

                    if not fetch_result:
                        self.job.errors.append(f"Failed to fetch: {current_url}")
                        continue

                    html_content, status_code = fetch_result

                    # Parse clean content, metadata, and links
                    try:
                        parsed_page = ContentExtractor.parse_page(html_content, current_url, status_code)
                        parsed_page["depth"] = depth
                        content_hash = URLCatalog.compute_hash(parsed_page["content"])
                        parsed_page["content_hash"] = content_hash

                        # Check change status against persistent catalog
                        change_status = self.catalog.check_change_status(current_url, content_hash)

                        if not self.job.force_recrawl and change_status == "unchanged":
                            self.job.pages_skipped += 1
                            parsed_page["change_status"] = "skipped_unchanged"
                            self.job.log(f"⏩ [Skipped - No Change]: {current_url}")
                        else:
                            if change_status == "updated":
                                self.job.pages_updated += 1
                                parsed_page["change_status"] = "updated"
                                self.job.log(f"🔄 [Updated - New Content]: '{parsed_page['title']}' ({parsed_page['word_count']} words)")
                            else:
                                self.job.pages_new += 1
                                parsed_page["change_status"] = "new"
                                self.job.log(f"✨ [New Page]: '{parsed_page['title']}' ({parsed_page['word_count']} words)")

                            # Record/update in persistent catalog
                            self.catalog.record_url(
                                url=current_url,
                                content_hash=content_hash,
                                domain=self.frontier.clean_domain,
                                title=parsed_page["title"],
                                job_id=self.job.job_id
                            )

                        self.job.pages.append(parsed_page)
                        self.frontier.pages_crawled += 1
                        self.job.pages_crawled = self.frontier.pages_crawled

                        # Add newly found internal links into the queue
                        for link_obj in parsed_page["discovered_links"]:
                            href = link_obj["href"]
                            self.frontier.add_url(href, depth=depth + 1, base_url=current_url)

                    except Exception as e:
                        err_msg = f"Parsing error on {current_url}: {str(e)}"
                        self.job.errors.append(err_msg)
                        self.job.log(err_msg)

                    # Politeness delay between requests
                    if self.frontier.has_more() and self.job.crawl_delay > 0:
                        await asyncio.sleep(self.job.crawl_delay)

            # Mark completed or stopped
            if self.job.is_cancelled:
                self.job.status = "stopped"
                self.job.completed_at = datetime.now().isoformat()
                self.job.duration_seconds = time.time() - start_time
                self.job.log(
                    f"🛑 Crawl stopped by user! Total: {self.job.pages_crawled} "
                    f"(New: {self.job.pages_new}, Updated: {self.job.pages_updated}, Skipped: {self.job.pages_skipped}) "
                    f"in {self.job.duration_seconds:.1f}s"
                )
            else:
                self.job.status = "completed"
                self.job.completed_at = datetime.now().isoformat()
                self.job.duration_seconds = time.time() - start_time
                self.job.log(
                    f"Crawl completed! Total: {self.job.pages_crawled} "
                    f"(New: {self.job.pages_new}, Updated: {self.job.pages_updated}, Skipped: {self.job.pages_skipped}) "
                    f"in {self.job.duration_seconds:.1f}s"
                )

        except Exception as e:
            self.job.status = "failed"
            self.job.completed_at = datetime.now().isoformat()
            self.job.duration_seconds = time.time() - start_time
            err_msg = f"Fatal crawler error: {str(e)}"
            self.job.errors.append(err_msg)
            self.job.log(err_msg)

        finally:
            if playwright_instance:
                await playwright_context.__aexit__(None, None, None)

            # Persist to UTF-8 JSON file
            try:
                stats = {
                    "started_at": self.job.started_at,
                    "completed_at": self.job.completed_at,
                    "duration_seconds": self.job.duration_seconds,
                    "max_pages_limit": self.job.max_pages,
                    "max_depth_limit": self.job.max_depth,
                    "render_js": self.job.render_js,
                    "force_recrawl": self.job.force_recrawl,
                    "include_subdomains": self.job.include_subdomains,
                    "pages_new": self.job.pages_new,
                    "pages_updated": self.job.pages_updated,
                    "pages_skipped": self.job.pages_skipped,
                    "errors": self.job.errors
                }
                saved_path = self.storage.save_crawl_job(
                    job_id=self.job.job_id,
                    seed_url=self.job.seed_url,
                    status=self.job.status,
                    pages=self.job.pages,
                    stats=stats
                )
                self.job.saved_file_path = saved_path
                self.job.log(f"Dataset successfully saved to: {saved_path}")
            except Exception as e:
                self.job.log(f"Failed to save JSON dataset: {str(e)}")

