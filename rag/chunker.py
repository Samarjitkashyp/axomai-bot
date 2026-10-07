import re
from typing import List, Dict, Any


class TextChunker:
    """
    Intelligently chunks long text documents into overlapping segments,
    respecting sentence boundaries across English, Assamese, and Hindi.
    """

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 120):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(self, text: str) -> List[str]:
        """
        Splits text into chunks respecting paragraph and sentence boundaries.
        Supports both English periods (.) and Indian danda (।).
        """
        if not text or not text.strip():
            return []

        text = text.strip()
        if len(text) <= self.chunk_size:
            return [text]

        # Split into paragraphs or logical blocks first
        paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]

        chunks = []
        current_chunk = ""

        for para in paragraphs:
            # If paragraph fits inside current chunk
            if len(current_chunk) + len(para) + 1 <= self.chunk_size:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
            else:
                # If current chunk has enough content, save it
                if current_chunk:
                    chunks.append(current_chunk.strip())

                # If paragraph itself is longer than chunk_size, split by sentences
                if len(para) > self.chunk_size:
                    # Regex splits on English period, exclamation, question mark, or Indian danda (।)
                    sentences = re.split(r'(?<=[.!?।])\s+', para)
                    sub_chunk = ""
                    for s in sentences:
                        if len(sub_chunk) + len(s) + 1 <= self.chunk_size:
                            sub_chunk = f"{sub_chunk} {s}".strip()
                        else:
                            if sub_chunk:
                                chunks.append(sub_chunk)
                            # Start next with overlap if possible
                            sub_chunk = s
                    if sub_chunk:
                        current_chunk = sub_chunk
                else:
                    # Handle sliding overlap from previous chunk if applicable
                    if chunks and self.chunk_overlap > 0:
                        prev_tail = chunks[-1][-self.chunk_overlap:]
                        current_chunk = prev_tail + " " + para
                    else:
                        current_chunk = para

        if current_chunk and current_chunk not in chunks:
            chunks.append(current_chunk.strip())

        return chunks

    def chunk_crawl_job(self, job_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Processes an entire crawled JSON document, converting all pages into
        structured chunks with full source traceability.
        """
        job_id = job_data.get("job_id", "unknown")
        pages = job_data.get("pages", [])

        all_chunks = []

        for page_idx, page in enumerate(pages):
            url = page.get("url", "")
            title = page.get("title", "")
            content = page.get("content", "")

            if not content.strip():
                continue

            text_chunks = self.chunk_text(content)

            for chunk_idx, chunk in enumerate(text_chunks):
                chunk_id = f"{job_id}_p{page_idx}_c{chunk_idx}"
                all_chunks.append({
                    "chunk_id": chunk_id,
                    "job_id": job_id,
                    "url": url,
                    "title": title,
                    "chunk_index": chunk_idx,
                    "text": chunk,
                    "char_count": len(chunk)
                })

        return all_chunks
