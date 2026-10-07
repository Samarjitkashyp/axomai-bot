# -*- coding: utf-8 -*-
"""Automatic clean-up of old backups, controlled from the Axom AI admin panel (Settings > Global Feature Flags > Bot backup clean-up).

The admin panel stores two settings in the SystemSetting table (superadmin_systemsetting):
    bot_cleanup_mode   off | dry_run | on        (default off)
    bot_cleanup_days   age in days               (default 7, never below 3)
The bot's scheduler calls maybe_run() every hour; it works at most about once a day and writes a report that the admin page shows
(cleanup_report.json next to this app). In dry_run mode nothing is deleted, the report only lists what would be.

Safety:
  * only direct children of BACKUP_DIR are looked at; symbolic links are never followed or deleted;
  * backups are grouped by kind (the name without its timestamp, e.g. "axomai-bot-code.tgz"); the newest backup of every kind is
    always kept, older ones go when they are older than `days`;
  * a kind with a single backup is the only copy: it goes only when it is older than max(4 x days, 30) days;
  * nothing else on the server (live data, crawled_data, vector_store, profiles) is ever touched.
"""
import fcntl
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("axomai.cleanup")

BACKUP_DIR = os.getenv("BACKUP_DIR", "/home/admin/axom_backups")
REPORT_FILE = os.getenv("CLEANUP_REPORT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cleanup_report.json"))
MODES = ("off", "dry_run", "on")
MIN_DAYS = 3
DEFAULT_DAYS = 7
RUN_EVERY_HOURS = 20
TIMESTAMP = re.compile(r"-?\d{8}-\d{4}")


def kind_of(name: str) -> str:
    return TIMESTAMP.sub("", name) or name


def read_settings() -> Dict[str, Any]:
    """(mode, days) from the SystemSetting table; anything unreadable means off."""
    mode, days = "off", DEFAULT_DAYS
    try:
        from app.auth import get_db_conn
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM superadmin_systemsetting WHERE key IN ('bot_cleanup_mode', 'bot_cleanup_days')")
            rows = dict(cur.fetchall())
            cur.close()
        finally:
            conn.close()
        if rows.get("bot_cleanup_mode") in MODES:
            mode = rows["bot_cleanup_mode"]
        try:
            days = max(MIN_DAYS, int(rows.get("bot_cleanup_days", DEFAULT_DAYS)))
        except (TypeError, ValueError):
            days = DEFAULT_DAYS
    except Exception as e:
        log.warning("cleanup settings could not be read, staying off: %s", e)
        mode = "off"
    return {"mode": mode, "days": days}


def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def plan(days: int, backup_dir: str = BACKUP_DIR, now: Optional[float] = None) -> Dict[str, Any]:
    now = now if now is not None else time.time()
    days = max(MIN_DAYS, int(days))
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in os.scandir(backup_dir):
        if e.is_symlink():
            continue
        groups.setdefault(kind_of(e.name), []).append({"name": e.name, "path": e.path, "mtime": e.stat(follow_symlinks=False).st_mtime})
    delete, keep = [], 0
    for kind, items in groups.items():
        items.sort(key=lambda x: x["mtime"], reverse=True)
        single = len(items) == 1
        for i, it in enumerate(items):
            age = (now - it["mtime"]) / 86400.0
            if i == 0 and not single:
                keep += 1                                  # the newest backup of a kind is always kept
                continue
            limit = max(4 * days, 30) if single else days  # the only copy of its kind waits longer
            if age > limit:
                delete.append({"name": it["name"], "path": it["path"], "kind": kind, "age_days": round(age, 1), "size": _size(it["path"]),
                               "reason": ("only copy, older than %d days" if single else "a newer backup of the same kind exists, older than %d days") % limit})
            else:
                keep += 1
    return {"delete": delete, "kept": keep}


def run_cleanup(mode: str, days: int, backup_dir: str = BACKUP_DIR, now: Optional[float] = None) -> Dict[str, Any]:
    """mode 'dry_run' lists, 'on' deletes. Returns the report (also what the admin page shows)."""
    result = plan(days, backup_dir, now)
    base = os.path.realpath(backup_dir)
    errors, done = [], []
    for it in result["delete"]:
        if mode == "on":
            real = os.path.realpath(it["path"])
            if os.path.dirname(real) != base or os.path.islink(it["path"]):   # never outside the backup folder
                errors.append("skipped (outside the backup folder): %s" % it["name"])
                continue
            try:
                shutil.rmtree(it["path"]) if os.path.isdir(it["path"]) else os.remove(it["path"])
                done.append(it)
            except OSError as e:
                errors.append("%s: %s" % (it["name"], e))
        else:
            done.append(it)
    listing = [{k: v for k, v in it.items() if k != "path"} for it in done]
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode, "days": max(MIN_DAYS, int(days)),
            "deleted" if mode == "on" else "would_delete": listing, "kept": result["kept"], "freed_bytes": sum(i["size"] for i in done), "errors": errors}


def _write_report(report: Dict[str, Any], path: str = REPORT_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _last_report(path: str = REPORT_FILE) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def maybe_run(settings: Optional[Dict[str, Any]] = None, backup_dir: str = BACKUP_DIR, report_file: str = REPORT_FILE) -> Optional[Dict[str, Any]]:
    """Called by the scheduler every hour. Does nothing when off, or when it already ran for these settings in the last ~20 hours.
    A lock file keeps the two uvicorn workers from running it twice."""
    s = settings or read_settings()
    if s["mode"] == "off":
        return None
    last = _last_report(report_file)
    if last and last.get("mode") == s["mode"] and last.get("days") == s["days"]:
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])).total_seconds() / 3600.0
            if age_h < RUN_EVERY_HOURS:
                return None
        except Exception:
            pass
    lock = open(report_file + ".lock", "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        report = run_cleanup(s["mode"], s["days"], backup_dir)
        _write_report(report, report_file)
        log.info("Backup clean-up (%s, %d days): %d item(s), %.1f MB, %d error(s)", s["mode"], s["days"],
                 len(report.get("deleted", report.get("would_delete", []))), report["freed_bytes"] / 1e6, len(report["errors"]))
        return report
    finally:
        lock.close()
