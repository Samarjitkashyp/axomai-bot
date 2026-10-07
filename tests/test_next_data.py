# -*- coding: utf-8 -*-
"""Tests for the __NEXT_DATA__ fallback in crawler/extractor.py. Run from the bot folder:  python3 tests/test_next_data.py"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from crawler.extractor import ContentExtractor  # noqa: E402

ARTICLE = [
    "Kaziranga National Park is home to two thirds of the world's one horned rhinoceros population.",
    "The best time to visit the park is between November and April when the grass is short and the animals are easy to spot.",
    "Jeep safaris start at sunrise from the central range at Kohora and take about two hours.",
]


def next_page(page_props, body="<div id='__next'></div>"):
    data = {"props": {"pageProps": page_props}, "page": "/park", "buildId": "abc123xyz-build-identifier-value"}
    return "<html><head><title>Park</title></head><body>%s<script id=\"__NEXT_DATA__\" type=\"application/json\">%s</script></body></html>" % (body, json.dumps(data))


def test_client_rendered_page_gets_its_text_from_next_data():
    html = next_page({"title": "Kaziranga National Park guide", "article": {"body": "<p>" + "</p><p>".join(ARTICLE) + "</p>", "slug": "kaziranga-guide-for-travellers",
                      "image": "https://cdn.example/rhino-picture-large-size.jpg"}, "locale": "en-IN"})
    text = ContentExtractor.extract_content(html, "https://x.in/park")
    assert all(a in text for a in ARTICLE)
    assert "https://cdn.example" not in text and "kaziranga-guide-for-travellers" not in text and "abc123xyz" not in text


def test_page_with_real_html_text_is_not_touched():
    body = "<article><p>" + " ".join("word%d" % i for i in range(120)) + "</p></article>"
    html = next_page({"junk": "A long string in the page props that must not replace the normal text of the page body"}, body=body)
    text = ContentExtractor.extract_content(html, "https://x.in/p")
    assert "word50" in text and "must not replace" not in text


def test_broken_or_missing_next_data_changes_nothing():
    assert ContentExtractor.extract_next_data("<html><body>hello</body></html>") == ""
    assert ContentExtractor.extract_next_data('<script id="__NEXT_DATA__" type="application/json">{not json</script>') == ""
    assert ContentExtractor.extract_next_data(next_page({"count": 5, "ok": True})) == ""


def test_short_strings_and_duplicates_are_left_out():
    text = ContentExtractor.extract_next_data(next_page({"a": "Home", "b": ARTICLE[0], "c": ARTICLE[0], "d": ["12345678901234567890123456789", ARTICLE[1]]}))
    assert text.split("\n\n") == [ARTICLE[0], ARTICLE[1]]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print("%d tests passed" % n)
