import os
import json
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional

DEFAULT_CATALOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "crawled_data", "url_catalog.json")
)


class URLCatalog:
    """
    Maintains a persistent registry of all crawled URLs and their SHA-256 content hashes.
    Enables instant change-detection and smart skipping of unchanged web pages.
    """

    def __init__(self, catalog_path: str = DEFAULT_CATALOG_PATH):
        self.catalog_path = catalog_path
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        self.entries: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self.catalog_path):
            try:
                with open(self.catalog_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        try:
            with open(self.catalog_path, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[URLCatalog] Error saving catalog: {e}")

    @staticmethod
    def compute_hash(text: str) -> str:
        """
        Computes SHA-256 fingerprint of normalized clean text.
        """
        clean = " ".join(text.split()).strip()
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def _normalize_key(self, url: str) -> str:
        try:
            from crawler.frontier import URLFrontier
            return URLFrontier.normalize_url(url)
        except Exception:
            return url.strip()

    def get_entry(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves catalog record for a given URL (with automatic normalization).
        """
        norm_url = self._normalize_key(url)
        return self.entries.get(norm_url) or self.entries.get(url)

    def check_change_status(self, url: str, new_content_hash: str) -> str:
        """
        Compares new content hash with historical record:
        - "new": URL never crawled before
        - "unchanged": Content fingerprint matches previous crawl exactly
        - "updated": Content has been modified since last crawl
        """
        norm_url = self._normalize_key(url)
        entry = self.entries.get(norm_url) or self.entries.get(url)
        if not entry:
            return "new"

        old_hash = entry.get("content_hash")
        if old_hash == new_content_hash:
            return "unchanged"

        return "updated"

    def record_url(
        self,
        url: str,
        content_hash: str,
        domain: str,
        title: str,
        job_id: str
    ):
        """
        Saves or updates URL fingerprint in persistent catalog.
        """
        norm_url = self._normalize_key(url)
        self.entries[norm_url] = {
            "content_hash": content_hash,
            "domain": domain,
            "title": title,
            "job_id": job_id,
            "last_crawled_at": datetime.now().isoformat()
        }
        self._save()

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns summary of indexed URLs in the catalog.
        """
        return {
            "total_known_urls": len(self.entries),
            "catalog_path": self.catalog_path
        }
