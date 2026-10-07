# -*- coding: utf-8 -*-
"""Tests for app/cleanup.py (backup clean-up). Run from the bot folder:  python3 tests/test_cleanup.py"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import cleanup  # noqa: E402

NOW = 1_800_000_000.0
DAY = 86400.0


def make(d, name, age_days, size=10, is_dir=False):
    p = os.path.join(d, name)
    if is_dir:
        os.makedirs(p)
        open(os.path.join(p, "f"), "w").write("x" * size)
    else:
        open(p, "w").write("x" * size)
    t = NOW - age_days * DAY
    os.utime(p, (t, t))
    return p


def test_kind_is_the_name_without_its_timestamp():
    assert cleanup.kind_of("axomai-bot-code-20261007-1225.tgz") == "axomai-bot-code.tgz"
    assert cleanup.kind_of("axomai-bot-20261007-1218") == "axomai-bot"
    assert cleanup.kind_of(".next.bak-20261006-2101") == ".next.bak"
    assert cleanup.kind_of("axomai-rebrand.patch") == "axomai-rebrand.patch"


def test_newest_of_a_kind_is_kept_and_old_siblings_go():
    with tempfile.TemporaryDirectory() as d:
        make(d, "code-20260101-0000.tgz", 100)
        make(d, "code-20260201-0000.tgz", 50)
        make(d, "code-20260301-0000.tgz", 40)
        make(d, "code-20260401-0000.tgz", 2)      # the newest of its kind: always kept
        names = [x["name"] for x in cleanup.plan(7, d, NOW)["delete"]]
        assert sorted(names) == ["code-20260101-0000.tgz", "code-20260201-0000.tgz", "code-20260301-0000.tgz"]   # the 2-day-old one is the newest: kept
    with tempfile.TemporaryDirectory() as d:
        make(d, "code-a-20260101-0000.tgz", 100)
        make(d, "code-a-20260301-0000.tgz", 40)
        names = [x["name"] for x in cleanup.plan(7, d, NOW)["delete"]]
        assert names == ["code-a-20260101-0000.tgz"]            # 40 days old but the newest of its kind


def test_the_only_copy_waits_four_times_longer():
    with tempfile.TemporaryDirectory() as d:
        make(d, "db-20260101-0000.json", 20)
        assert cleanup.plan(7, d, NOW)["delete"] == []                       # 20 days < max(28, 30)
        make(d, "other-20260101-0000.json", 31)
        assert [x["name"] for x in cleanup.plan(7, d, NOW)["delete"]] == ["other-20260101-0000.json"]


def test_dry_run_deletes_nothing_and_on_deletes_files_and_folders():
    with tempfile.TemporaryDirectory() as d:
        make(d, "bot-20260101-0000", 100, is_dir=True)
        make(d, "bot-20260301-0000", 1, is_dir=True)
        make(d, "x-20260101-0000.tgz", 100)
        make(d, "x-20260301-0000.tgz", 1)
        r = cleanup.run_cleanup("dry_run", 7, d, NOW)
        assert sorted(i["name"] for i in r["would_delete"]) == ["bot-20260101-0000", "x-20260101-0000.tgz"] and "deleted" not in r
        assert len(os.listdir(d)) == 4
        r = cleanup.run_cleanup("on", 7, d, NOW)
        assert sorted(i["name"] for i in r["deleted"]) == ["bot-20260101-0000", "x-20260101-0000.tgz"] and r["errors"] == []
        assert sorted(os.listdir(d)) == ["bot-20260301-0000", "x-20260301-0000.tgz"]
        assert r["freed_bytes"] > 0 and r["kept"] == 2


def test_days_never_below_three_and_symlinks_are_ignored():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
        make(d, "a-20260101-0000.tgz", 2.5)
        make(d, "a-20260301-0000.tgz", 0.1)
        assert cleanup.plan(1, d, NOW)["delete"] == []                       # 1 day is raised to 3: 2.5 days old is kept
        target = make(outside, "precious.txt", 500)
        os.symlink(target, os.path.join(d, "a-20250101-0000.tgz"))
        os.utime(os.path.join(d, "a-20250101-0000.tgz"), (NOW - 500 * DAY, NOW - 500 * DAY), follow_symlinks=False)
        r = cleanup.run_cleanup("on", 7, d, NOW)
        assert os.path.exists(target) and "a-20250101-0000.tgz" not in [i["name"] for i in r["deleted"]]


def test_maybe_run_is_off_by_default_runs_once_a_day_and_writes_the_report():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as r:
        make(d, "a-20260101-0000.tgz", 100)
        make(d, "a-20260301-0000.tgz", 1)
        report_file = os.path.join(r, "report.json")
        assert cleanup.maybe_run({"mode": "off", "days": 7}, d, report_file) is None and not os.path.exists(report_file)
        rep = cleanup.maybe_run({"mode": "dry_run", "days": 7}, d, report_file)
        assert rep and rep["mode"] == "dry_run" and json.load(open(report_file))["mode"] == "dry_run"
        assert cleanup.maybe_run({"mode": "dry_run", "days": 7}, d, report_file) is None       # already ran in the last 20 hours
        assert cleanup.maybe_run({"mode": "on", "days": 7}, d, report_file) is not None        # a changed setting runs at once


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
