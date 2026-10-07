import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from threading import Lock

from crawler.engine import CrawlerJob, CrawlerEngine
from crawler.storage import StorageManager

logger = logging.getLogger("axomai.scheduler")

SCHEDULE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "crawled_data", "schedules.json")
)
INTERVAL_DAYS = 10
MAX_CONCURRENT_AUTO = 1
AUTO_MAX_PAGES = 100
AUTO_MAX_DEPTH = 3
AUTO_CRAWL_DELAY = 2.0
CHECK_INTERVAL_SECONDS = 3600  # check every hour

_lock = Lock()
_running_auto_crawl: Optional[str] = None


def _load_schedules() -> Dict[str, dict]:
    if not os.path.exists(SCHEDULE_FILE):
        return {}
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_schedules(data: Dict[str, dict]):
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def register_crawl(seed_url: str, enabled: bool = True):
    with _lock:
        schedules = _load_schedules()
        key = seed_url.rstrip("/")
        now = datetime.utcnow().isoformat()
        if key in schedules:
            schedules[key]["last_crawled"] = now
            schedules[key]["next_run"] = (
                datetime.utcnow() + timedelta(days=INTERVAL_DAYS)
            ).isoformat()
        else:
            schedules[key] = {
                "seed_url": seed_url,
                "enabled": enabled,
                "last_crawled": now,
                "next_run": (
                    datetime.utcnow() + timedelta(days=INTERVAL_DAYS)
                ).isoformat(),
                "last_auto_status": None,
                "total_auto_runs": 0,
            }
        _save_schedules(schedules)


def set_schedule_enabled(seed_url: str, enabled: bool):
    with _lock:
        schedules = _load_schedules()
        key = seed_url.rstrip("/")
        if key in schedules:
            schedules[key]["enabled"] = enabled
            _save_schedules(schedules)
            return True
        return False


def remove_schedule(seed_url: str):
    with _lock:
        schedules = _load_schedules()
        key = seed_url.rstrip("/")
        if key in schedules:
            del schedules[key]
            _save_schedules(schedules)


def get_all_schedules() -> List[dict]:
    schedules = _load_schedules()
    result = []
    for key, val in schedules.items():
        entry = dict(val)
        entry["seed_url"] = entry.get("seed_url", key)
        next_dt = entry.get("next_run")
        if next_dt:
            try:
                remaining = datetime.fromisoformat(next_dt) - datetime.utcnow()
                entry["days_remaining"] = max(0, remaining.days)
            except Exception:
                entry["days_remaining"] = None
        result.append(entry)
    return result


async def _run_auto_crawl(
    seed_url: str,
    storage_manager: StorageManager,
    active_jobs: Dict[str, CrawlerJob],
):
    global _running_auto_crawl
    job_id = f"auto_{uuid.uuid4().hex[:8]}"
    logger.info("Auto-crawl starting: %s (job %s)", seed_url, job_id)

    job = CrawlerJob(
        job_id=job_id,
        seed_url=seed_url,
        max_pages=AUTO_MAX_PAGES,
        max_depth=AUTO_MAX_DEPTH,
        crawl_delay=AUTO_CRAWL_DELAY,
        render_js=False,
        force_recrawl=False,
    )
    active_jobs[job_id] = job

    engine = CrawlerEngine(job=job, storage_manager=storage_manager)
    try:
        await engine.run()
        status = "completed"
    except Exception as e:
        logger.error("Auto-crawl failed for %s: %s", seed_url, e)
        status = "failed"
    finally:
        _running_auto_crawl = None

    if status == "completed":
        try:
            from app.main import _push_to_chat
            await _push_to_chat(job_id, seed_url)
        except Exception as e:
            logger.error("Auto-crawl chat import failed for %s: %s", seed_url, e)

    with _lock:
        schedules = _load_schedules()
        key = seed_url.rstrip("/")
        if key in schedules:
            schedules[key]["last_crawled"] = datetime.utcnow().isoformat()
            schedules[key]["next_run"] = (
                datetime.utcnow() + timedelta(days=INTERVAL_DAYS)
            ).isoformat()
            schedules[key]["last_auto_status"] = status
            schedules[key]["total_auto_runs"] = (
                schedules[key].get("total_auto_runs", 0) + 1
            )
            _save_schedules(schedules)

    logger.info(
        "Auto-crawl finished: %s — %s (pages: %d new, %d skipped)",
        seed_url, status, job.pages_new, job.pages_skipped,
    )


async def check_and_run(
    storage_manager: StorageManager,
    active_jobs: Dict[str, CrawlerJob],
):
    global _running_auto_crawl
    if _running_auto_crawl:
        return

    schedules = _load_schedules()
    now = datetime.utcnow()

    for key, entry in schedules.items():
        if not entry.get("enabled", False):
            continue
        next_run = entry.get("next_run")
        if not next_run:
            continue
        try:
            next_dt = datetime.fromisoformat(next_run)
        except Exception:
            continue
        if now >= next_dt:
            _running_auto_crawl = key
            seed_url = entry.get("seed_url", key)
            asyncio.create_task(
                _run_auto_crawl(seed_url, storage_manager, active_jobs)
            )
            return


async def scheduler_loop(
    storage_manager: StorageManager,
    active_jobs: Dict[str, CrawlerJob],
):
    logger.info(
        "Scheduler started — checking every %d seconds, interval %d days",
        CHECK_INTERVAL_SECONDS, INTERVAL_DAYS,
    )
    while True:
        try:
            await check_and_run(storage_manager, active_jobs)
        except Exception as e:
            logger.error("Scheduler tick error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
