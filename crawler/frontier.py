import re
from urllib.parse import urlparse, urlunparse, urljoin, parse_qsl, urlencode
from collections import deque
from typing import Optional, Set, Tuple

# Binary / non-HTML file extensions to ignore during web page crawling
IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov", ".flv",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2",
    ".exe", ".msi", ".bin", ".dmg", ".apk",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot"
}

# Common tracking / duplicate query parameters to strip
IGNORED_QUERY_PARAMS = {
    "m", "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "fbclid", "gclid", "ref", "source", "showcomment"
}


class URLFrontier:
    """
    Manages the URL crawling queue (BFS order), prevents duplicate visits,
    and enforces strict same-domain boundary rules.
    """

    @staticmethod
    def _root_domain(netloc: str) -> str:
        """Extract root domain (e.g. news18.com from assam.news18.com)."""
        parts = netloc.replace("www.", "").split(".")
        # AXOMAI-FIX: under co.in, gov.in, org.in, co.uk ... the registrable domain has THREE labels (assam.gov.in), otherwise
        # every .gov.in or .co.in site would count as the same site when "Include Subdomains" is on
        if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "edu", "ac", "res", "mil", "ind", "gen", "firm", "or", "ne", "go"):
            return ".".join(parts[-3:])
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc

    def __init__(self, seed_url: str, max_pages: int = 50, max_depth: int = 3, include_subdomains: bool = False):
        parsed = urlparse(seed_url.strip())
        self.scheme = parsed.scheme.lower() if parsed.scheme else "https"
        self.base_domain = parsed.netloc.lower()
        self.clean_domain = self.base_domain.replace("www.", "")
        self.include_subdomains = include_subdomains
        self.root_domain = self._root_domain(self.clean_domain)

        self.seed_url = self.normalize_url(seed_url, base_netloc=self.base_domain)
        self.max_pages = max_pages
        self.max_depth = max_depth

        # FIFO queue storing (url, depth)
        self.queue: deque[Tuple[str, int]] = deque()
        # Set of normalized URLs seen (either queued or visited)
        self.seen_urls: Set[str] = set()
        # Count of pages actually crawled
        self.pages_crawled = 0

        # Seed the queue
        self.add_url(self.seed_url, depth=0)

    @classmethod
    def normalize_url(cls, url: str, base_netloc: Optional[str] = None) -> str:
        """
        Cleans and normalizes URL:
        - Drops URL fragments (#target)
        - Normalizes scheme and netloc to lowercase
        - Aligns netloc www. prefix with seed domain
        - Root domains always end with '/' (e.g., https://example.com/)
        - Subpaths remove trailing '/' (e.g., https://example.com/about)
        - Strips tracking query parameters (?m=1, ?utm_*, etc.)
        """
        try:
            parsed = urlparse(url.strip())
            scheme = (parsed.scheme or "https").lower()
            netloc = parsed.netloc.lower()

            if base_netloc:
                clean_base = base_netloc.lower().replace("www.", "")
                clean_net = netloc.replace("www.", "")
                if clean_net == clean_base:
                    netloc = base_netloc.lower()

            # Path normalization
            path = parsed.path
            if not path or path == "/":
                path = "/"
            elif path.endswith("/"):
                path = path[:-1]

            # Query normalization: remove tracking/device params
            clean_query = []
            if parsed.query:
                for k, v in parse_qsl(parsed.query, keep_blank_values=True):
                    k_lower = k.lower()
                    if k_lower not in IGNORED_QUERY_PARAMS and not k_lower.startswith("utm_"):
                        clean_query.append((k, v))
            query_str = urlencode(clean_query) if clean_query else ""

            return urlunparse((scheme, netloc, path, parsed.params, query_str, ""))
        except Exception:
            return url.strip()

    def is_same_domain(self, url: str) -> bool:
        """
        Checks if the candidate URL belongs to the seed domain.
        When include_subdomains is True, any subdomain of the root domain is accepted.
        """
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            if not netloc:
                return False
            clean_netloc = netloc.replace("www.", "")
            if self.include_subdomains:
                candidate_root = self._root_domain(clean_netloc)
                return candidate_root == self.root_domain
            return clean_netloc == self.clean_domain
        except Exception:
            return False

    def is_crawlable_url(self, url: str) -> bool:
        """
        Verifies if URL has a valid HTTP/HTTPS scheme and isn't a media/binary asset.
        """
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False

            path_lower = parsed.path.lower()
            for ext in IGNORED_EXTENSIONS:
                if path_lower.endswith(ext):
                    return False

            return True
        except Exception:
            return False

    def add_url(self, raw_url: str, depth: int, base_url: Optional[str] = None) -> bool:
        """
        Resolves relative URLs, checks domain/depth limits, and queues if unseen.
        Returns True if added, False otherwise.
        """
        if depth > self.max_depth:
            return False

        # Convert relative link to absolute link
        if base_url:
            resolved_url = urljoin(base_url, raw_url)
        else:
            resolved_url = raw_url

        normalized = self.normalize_url(resolved_url, base_netloc=self.base_domain)

        if not self.is_crawlable_url(normalized):
            return False

        if not self.is_same_domain(normalized):
            return False

        if normalized in self.seen_urls:
            return False

        # Mark seen and enqueue
        self.seen_urls.add(normalized)
        self.queue.append((normalized, depth))
        return True

    def get_next(self) -> Optional[Tuple[str, int]]:
        """
        Retrieves the next (url, depth) to crawl if limits allow.
        """
        if self.pages_crawled >= self.max_pages:
            return None

        if not self.queue:
            return None

        return self.queue.popleft()

    def has_more(self) -> bool:
        """
        Returns True if there are more URLs to visit and max_pages is not reached.
        """
        return bool(self.queue) and (self.pages_crawled < self.max_pages)
