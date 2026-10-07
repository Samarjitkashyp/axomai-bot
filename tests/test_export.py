# -*- coding: utf-8 -*-
"""Tests for app/export.py (content-only export). Run from the bot folder:  python3 tests/test_export.py"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.export import export_pages, page_name, page_text  # noqa: E402


def test_name_is_the_title_or_made_from_the_url():
    assert page_name({"title": "All Tours", "url": "https://x.in/all-tours"}) == "All Tours"
    assert page_name({"title": "https://x.in/all-tours", "url": "https://x.in/all-tours"}) == "All Tours"
    assert page_name({"title": "", "url": "https://x.in/about_us.html"}) == "About Us"
    assert page_name({"title": "", "url": "https://x.in/"}) == "Home"
    assert page_name({"title": "", "url": "https://x.in/%E0%A6%85%E0%A6%B8%E0%A6%AE"}) == "অসম"


def test_text_has_no_buttons_urls_or_repeated_heading():
    content = "\n\n".join(["Kaziranga Tour", "More", "https://x.in/book", "Day 1:", "Arrival at Guwahati and drive to Kaziranga", "Share on Facebook",
                          "Get a Free Quote", "More than 50 tourists visited last week", "Price Rs. 5000 per person"])
    text = page_text(content, "Kaziranga Tour")
    assert text == "\n\n".join(["Day 1:", "Arrival at Guwahati and drive to Kaziranga", "More than 50 tourists visited last week", "Price Rs. 5000 per person"])


def test_pages_without_text_are_left_out_and_nothing_else_is_exported():
    pages = [{"title": "A", "url": "https://x.in/a", "content": "A\n\nReal paragraph about the tour here", "links": ["https://x.in/z"], "word_count": 9},
             {"title": "Empty", "url": "https://x.in/b", "content": "More\n\nview more", "word_count": 2}]
    out = export_pages(pages)
    assert out == [{"page": "A", "content": "Real paragraph about the tour here"}]
    assert set(out[0]) == {"page", "content"}


def test_site_name_that_ends_most_titles_is_removed():
    pages = [{"title": "%s | My Voyage" % n, "url": "https://x.in/%s" % n.lower(), "content": "%s | My Voyage\n\nBody text of the page %s here" % (n, n)}
             for n in ("Home", "About Us", "Tours", "Blog")] + [{"title": "Odd page", "url": "https://x.in/odd", "content": "Odd page\n\nSome other body text here"}]
    out = export_pages(pages)
    assert [x["page"] for x in out] == ["Home", "About Us", "Tours", "Blog", "Odd page"]
    assert out[0]["content"] == "Body text of the page Home here"      # the long title line at the top is not repeated either


def test_no_suffix_is_removed_when_titles_differ():
    pages = [{"title": t, "url": "https://x.in/%d" % i, "content": t + "\n\nBody text number %d here" % i} for i, t in enumerate(["A - one", "B - two", "C - three"])]
    assert [x["page"] for x in export_pages(pages)] == ["A - one", "B - two", "C - three"]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
