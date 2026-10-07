import json
import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

BASE_OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "crawled_data"))


class StorageManager:
    """
    Handles persisting crawl jobs into structured UTF-8 JSON files.
    """

    def __init__(self, output_dir: str = BASE_OUTPUT_DIR):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """
        Replaces characters unsafe for file names.
        """
        return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)

    def get_file_path(self, job_id: str, seed_url: str) -> str:
        """
        Generates consistent JSON file path: <job_id>_<domain>.json
        """
        netloc = urlparse(seed_url).netloc.replace("www.", "")
        safe_domain = self.sanitize_filename(netloc or "webpage")
        filename = f"{job_id}_{safe_domain}.json"
        return os.path.join(self.output_dir, filename)

    def save_crawl_job(
        self,
        job_id: str,
        seed_url: str,
        status: str,
        pages: List[Dict[str, Any]],
        stats: Dict[str, Any]
    ) -> str:
        """
        Saves the complete crawl dataset in pure UTF-8 JSON format.
        Sanitizes page objects to remove discovered_links and runtime garbage,
        leaving pure clean URL, Title, and Article Content.
        """
        filepath = self.get_file_path(job_id, seed_url)

        # Sanitize pages: strip massive discovered_links arrays and internal runtime markers
        clean_pages = []
        for p in pages:
            clean_pages.append({
                "url": p.get("url", ""),
                "title": p.get("title", ""),
                "content": p.get("content", ""),
                "word_count": p.get("word_count", 0),
                "change_status": p.get("change_status", "new")
            })

        # AXOMAI-CLEAN: sites with a profile (crawler/site_profiles.json) are cleaned; the crawl as it came from the web is archived
        # in crawled_data_raw/ first, and any problem leaves the pages untouched. Other sites are saved exactly as before.
        cleaning = None
        try:
            from crawler.cleaner import apply_profile
            result = apply_profile(seed_url, clean_pages)
            if result is not None:
                if self._archive_raw(job_id, seed_url, status, clean_pages, stats):
                    clean_pages, cleaning = result
                else:
                    cleaning = {"applied": False, "reason": "the raw archive could not be written"}
        except Exception as e:  # never lose a crawl because of the cleaning
            cleaning = {"applied": False, "reason": "error: %s" % e}
        if cleaning is not None:
            stats = dict(stats, cleaning=cleaning)

        document = {
            "job_id": job_id,
            "seed_url": seed_url,
            "status": status,
            "total_pages_crawled": len(clean_pages),
            "created_at": stats.get("started_at", datetime.now().isoformat()),
            "completed_at": stats.get("completed_at", datetime.now().isoformat()),
            "duration_seconds": stats.get("duration_seconds", 0.0),
            "stats": stats,
            "pages": clean_pages
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False, indent=2)

        return filepath

    def _archive_raw(self, job_id: str, seed_url: str, status: str, pages: List[Dict[str, Any]], stats: Dict[str, Any]) -> bool:
        """Writes the crawl exactly as crawled to crawled_data_raw/ (next to crawled_data). Never overwrites a file."""
        try:
            raw_dir = os.path.join(os.path.dirname(self.output_dir), "crawled_data_raw")
            os.makedirs(raw_dir, exist_ok=True)
            path = os.path.join(raw_dir, os.path.basename(self.get_file_path(job_id, seed_url)))
            if os.path.exists(path):
                return True
            document = {
                "job_id": job_id, "seed_url": seed_url, "status": status, "total_pages_crawled": len(pages),
                "created_at": stats.get("started_at", datetime.now().isoformat()),
                "completed_at": stats.get("completed_at", datetime.now().isoformat()),
                "duration_seconds": stats.get("duration_seconds", 0.0), "stats": stats, "pages": pages,
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(document, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def get_crawl_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Locates and loads a saved crawl JSON by job_id.
        """
        for filename in os.listdir(self.output_dir):
            if filename.startswith(f"{job_id}_") and filename.endswith(".json"):
                full_path = os.path.join(self.output_dir, filename)
                with open(full_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def list_saved_jobs(self) -> List[Dict[str, Any]]:
        """
        Returns list of metadata for all saved crawl files in storage.
        """
        results = []
        if not os.path.exists(self.output_dir):
            return results

        for filename in os.listdir(self.output_dir):
            if filename.endswith(".json"):
                full_path = os.path.join(self.output_dir, filename)
                try:
                    stats = os.stat(full_path)
                    with open(full_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        job_stats = data.get("stats", {})
                        results.append({
                            "job_id": data.get("job_id"),
                            "seed_url": data.get("seed_url"),
                            "status": data.get("status"),
                            "total_pages": data.get("total_pages_crawled", 0),
                            "pages_new": job_stats.get("pages_new", 0),
                            "pages_updated": job_stats.get("pages_updated", 0),
                            "pages_skipped": job_stats.get("pages_skipped", 0),
                            "file_size_bytes": stats.st_size,
                            "completed_at": data.get("completed_at"),
                            "filename": filename
                        })
                except Exception:
                    continue

        # Sort descending by completion date
        results.sort(key=lambda x: x.get("completed_at") or "", reverse=True)
        return results
