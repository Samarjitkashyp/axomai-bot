import re
from typing import Dict, List, Any, Optional
from bs4 import BeautifulSoup
import trafilatura


class ContentExtractor:
    """
    Extracts clean text, metadata, and internal links from HTML.
    Fully preserves multilingual Unicode text (Assamese, Hindi, Bengali, English).
    """

    @staticmethod
    def extract_metadata(soup: BeautifulSoup, fallback_url: str) -> Dict[str, Any]:
        """
        Extracts title, meta description, and canonical URL.
        """
        # 1. Title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        elif soup.find("meta", property="og:title"):
            title = soup.find("meta", property="og:title").get("content", "").strip()
        elif soup.find("h1"):
            title = soup.find("h1").get_text(strip=True)

        # 2. Description
        description = ""
        desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if desc_tag and desc_tag.get("content"):
            description = desc_tag["content"].strip()
        else:
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                description = og_desc["content"].strip()

        # 3. Canonical URL
        canonical = ""
        canon_tag = soup.find("link", rel="canonical")
        if canon_tag and canon_tag.get("href"):
            canonical = canon_tag["href"].strip()

        return {
            "title": title or fallback_url,
            "description": description,
            "canonical": canonical
        }

    @classmethod
    def clean_text_fallback(cls, soup: BeautifulSoup) -> str:
        """
        Extracts clean body text by targeting main content containers and
        decomposing layout, headers, footers, and sidebars.
        """
        # Create a copy so we don't modify the original soup
        content_soup = BeautifulSoup(str(soup), "lxml")

        # Decompose non-content layout junk, sidebars, and widgets
        junk_tags = ["script", "style", "noscript", "svg", "header", "footer", "nav", "aside", "form", "iframe"]
        for tag in content_soup(junk_tags):
            tag.decompose()

        for widget in content_soup.select(".sidebar, .widget, .comments, .blog-pager, .feed-links, .popular-posts, [id*='sidebar']"):
            widget.decompose()

        # Check for explicit main post/article containers
        for selector in [".post-body", ".entry-content", "article", "main", "[role='main']", "#main", "#content"]:
            elem = content_soup.select_one(selector)
            if elem:
                text = elem.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                clean_body = "\n\n".join(lines)
                if len(clean_body) > 60:
                    return clean_body

        text = content_soup.get_text(separator="\n", strip=True)
        # Collapse multi-newlines into clean paragraphs
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n\n".join(lines)

    @classmethod
    def extract_content(cls, html: str, url: str) -> str:
        """
        Extracts clean main content. Prioritizes primary article containers,
        complements with Trafilatura, and falls back to clean BS4 extraction.
        """
        if not html:
            return ""

        soup = BeautifulSoup(html, "lxml")
        fallback_text = cls.clean_text_fallback(soup)

        # Try Trafilatura for article/body isolation
        extracted = trafilatura.extract(
            html,
            url=url,
            include_links=False,
            include_images=False,
            output_format="txt"
        )
        traf_text = extracted.strip() if extracted else ""

        # If we have a dedicated article body with more context than Trafilatura, use it
        if fallback_text and len(fallback_text) >= len(traf_text):
            return fallback_text
        elif traf_text and len(traf_text) > 50:
            return traf_text

        return fallback_text or traf_text

    DOC_EXTENSIONS = (
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".zip", ".rar", ".7z", ".csv", ".odt", ".ods", ".rtf"
    )

    @classmethod
    def extract_links(cls, soup: BeautifulSoup) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """
        Extracts all href links with their anchor text, categorizing web pages and documents/PDFs.
        """
        web_links = []
        doc_links = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            text = a_tag.get_text(strip=True)
            href_lower = href.lower().split("?")[0]
            if href_lower.endswith(cls.DOC_EXTENSIONS):
                doc_links.append({"href": href, "text": text or "Download Document"})
            else:
                web_links.append({"href": href, "text": text})
        return web_links, doc_links

    @classmethod
    def parse_page(cls, html: str, url: str, status_code: int = 200) -> Dict[str, Any]:
        """
        Full page parser returning structured page document.
        """
        soup = BeautifulSoup(html, "lxml")

        metadata = cls.extract_metadata(soup, fallback_url=url)
        content = cls.extract_content(html, url=url)
        discovered_links, document_links = cls.extract_links(soup)

        # Word count calculation (Unicode-safe splitting)
        words = re.findall(r"\w+", content, re.UNICODE)
        word_count = len(words)

        return {
            "url": url,
            "status_code": status_code,
            "title": metadata["title"],
            "description": metadata["description"],
            "canonical": metadata["canonical"],
            "content": content,
            "word_count": word_count,
            "links_found_count": len(discovered_links),
            "discovered_links": discovered_links,
            "documents_found_count": len(document_links),
            "document_links": document_links
        }
