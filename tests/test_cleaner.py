# -*- coding: utf-8 -*-
"""Tests for crawler/cleaner.py and the hook in crawler/storage.py. Run from the bot folder:  python3 -m pytest tests/test_cleaner.py
(or, without pytest:  python3 tests/test_cleaner.py)"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from crawler import cleaner  # noqa: E402
from crawler.storage import StorageManager  # noqa: E402

MENU = "Home\nTours\nAbout us\nEnquire Now\nview more"
PROF = {"enabled": True, "threshold": 0.30, "junk_urls": ["/new-demo/", "[?&]e-page-"], "keep_pages": ["/all-tours$"], "terms": ["Kaziranga"]}


def page(url, title, body, menu=MENU):
    return {"url": url, "title": title, "content": "\n\n".join([title] + body + menu.split("\n")), "word_count": 0, "change_status": "new"}


def site(n=14):
    pages = [page("https://x.in/tour-%d" % i, "Tour %d" % i, ["Day 1:", "Unique plan for tour %d at Kaziranga with %d elephants" % (i, i), "Price Rs. %d000 per person" % (i + 5)]) for i in range(n)]
    pages.append(page("https://x.in/all-tours", "All tours", ["Tour list"]))
    return pages


def test_menu_removed_unique_text_kept():
    out, summary = cleaner.clean_pages(site(), PROF)
    assert summary["applied"]
    p = [x for x in out if x["url"] == "https://x.in/tour-3"][0]
    assert "Enquire Now" not in p["content"] and "view more" not in p["content"]
    assert "Unique plan for tour 3" in p["content"]
    assert "Tour 3" in p["content"]                      # the page title is protected
    assert "Day 1:" in p["content"]                      # day labels are protected
    assert "Price Rs. 8000 per person" in p["content"]   # lines with a price are protected
    assert p["word_count"] == cleaner.words(p["content"])


def test_listing_page_never_cleaned():
    out, _ = cleaner.clean_pages(site(), PROF)
    hub = [x for x in out if x["url"] == "https://x.in/all-tours"][0]
    assert "Enquire Now" in hub["content"]


def test_junk_urls_and_exact_copies_dropped():
    pages = site() + [page("https://x.in/new-demo/tour-1", "Tour 1", ["junk"]), page("https://x.in/all-tours?e-page-9=2", "p2", ["junk"]),
                      dict(page("https://x.in/copy", "Tour 1", ["Day 1:", "Unique plan for tour 1 at Kaziranga with 1 elephants", "Price Rs. 6000 per person"]))]
    # the copy has the same lines as tour-1 except the title line is identical too -> exact copy
    out, summary = cleaner.clean_pages(pages, PROF)
    urls = [x["url"] for x in out]
    assert "https://x.in/new-demo/tour-1" not in urls and "https://x.in/all-tours?e-page-9=2" not in urls
    assert "https://x.in/copy" not in urls
    assert len(summary["pages_dropped"]) == 3


def test_a_line_on_one_page_is_never_removed():
    pages = site()
    pages[0]["content"] += "\n\nThis sentence is only here"
    out, _ = cleaner.clean_pages(pages, PROF)
    assert "This sentence is only here" in [x for x in out if x["url"] == pages[0]["url"]][0]["content"]


def test_few_pages_no_boilerplate_removal():
    out, _ = cleaner.clean_pages(site(5), PROF)
    assert "Enquire Now" in out[0]["content"]


def test_gate_must_keep_phrase_is_lost():
    prof = dict(PROF, terms=["Enquire Now"])  # present before; the menu removal would lose it everywhere except the hub page
    pages = [p for p in site() if p["url"] != "https://x.in/all-tours"]
    out, summary = cleaner.clean_pages(pages, prof)
    assert summary["applied"] is False and out is pages


def test_gate_too_much_removed():
    filler = lambda i: ["u%d_%d" % (i, k) for k in range(7)]  # short unique lines first, so the menu lines are not at the top of the page
    pages = [{"url": "https://x.in/p%d" % i, "title": "t%d" % i, "content": "\n\n".join(filler(i) + ["menu a b c d e f g h i j", "menu k l m n o p q r s t", "x%d" % i]), "word_count": 0, "change_status": "new"} for i in range(12)]
    out, summary = cleaner.clean_pages(pages, dict(PROF, terms=[], keep_pages=[]))
    assert summary["applied"] is False and out is pages


def test_gate_is_not_fooled_by_many_dropped_junk_pages():
    keep = site()
    junk = [page("https://x.in/new-demo/j%d" % i, "J%d" % i, ["lots of words " * 60 + str(i)]) for i in range(30)]   # thousands of words, all dropped
    out, summary = cleaner.clean_pages(keep + junk, PROF)
    assert summary["applied"] is True and len(out) == len(keep)


def test_same_line_twice_on_one_page_is_kept_once():
    pages = site()
    twice = "A long sentence that appears twice on this page."
    pages[0]["content"] = "\n\n".join([pages[0]["content"], twice, twice, "short", "short"])
    out, _ = cleaner.clean_pages(pages, PROF)
    text = [x for x in out if x["url"] == pages[0]["url"]][0]["content"]
    assert text.count("A long sentence that appears twice on this page.") == 1
    assert text.count("short") == 2       # lines under 4 words are not touched


def test_unknown_site_is_untouched(tmp_path=None):
    assert cleaner.apply_profile("https://no-profile.example/", site()) is None


def test_storage_hook_archives_raw_and_cleans():
    with tempfile.TemporaryDirectory() as d:
        out_dir = os.path.join(d, "crawled_data")
        old = cleaner.PROFILE_FILE
        cleaner.PROFILE_FILE = os.path.join(d, "profiles.json")
        json.dump({"x.in": PROF}, open(cleaner.PROFILE_FILE, "w"))
        try:
            sm = StorageManager(output_dir=out_dir)
            path = sm.save_crawl_job("job_t1", "https://x.in/", "completed", site(), {"started_at": "now"})
            cleaned = json.load(open(path, encoding="utf-8"))
            raw = json.load(open(os.path.join(d, "crawled_data_raw", os.path.basename(path)), encoding="utf-8"))
            assert cleaned["stats"]["cleaning"]["applied"] is True
            assert "Enquire Now" in raw["pages"][0]["content"] and "Enquire Now" not in cleaned["pages"][0]["content"]
            assert raw["total_pages_crawled"] == len(site())
            # a site without a profile: saved as before, no raw archive, no cleaning entry
            path2 = sm.save_crawl_job("job_t2", "https://other.example/", "completed", site(), {"started_at": "now"})
            other = json.load(open(path2, encoding="utf-8"))
            assert "cleaning" not in other["stats"] and "Enquire Now" in other["pages"][0]["content"]
            assert not os.path.exists(os.path.join(d, "crawled_data_raw", os.path.basename(path2)))
        finally:
            cleaner.PROFILE_FILE = old


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
