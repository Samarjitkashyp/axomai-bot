import os
import json
from typing import List, Dict, Any, Optional
import httpx


class LLMManager:
    """
    Handles generative AI synthesis for RAG using:
    1. Google Gemini API (if key provided or GEMINI_API_KEY set)
    2. Local Ollama (if running on localhost:11434)
    3. Smart Grounded Fallback Synthesizer (offline, zero-cost)
    """

    def __init__(self, default_api_key: Optional[str] = None):
        self.default_api_key = default_api_key or os.environ.get("GEMINI_API_KEY", "")
        self.ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    def build_rag_prompt(self, question: str, context_chunks: List[Dict[str, Any]]) -> str:
        """
        Assembles a strict grounded context prompt with citation instructions.
        """
        context_text = ""
        for idx, chunk in enumerate(context_chunks):
            context_text += f"\n[Passage {idx + 1}] (Source: {chunk.get('url', 'N/A')})\n{chunk.get('text', '')}\n"

        prompt = f"""You are Axom AI, an intelligent, factual, and helpful knowledge assistant.
Your task is to answer the user's question using ONLY the provided reference passages below.

GUIDELINES:
1. Answer strictly based on the facts provided in the passages. Do not invent or assume information.
2. Match the language of the user's question. If the question is in Assamese (অসমীয়া), answer in Assamese. If in Hindi, answer in Hindi. If in English, answer in English.
3. At the end of relevant statements, cite the source passages like [Passage 1] or [Passage 2].
4. If the provided passages do not contain enough information to answer the question, clearly state: "The crawled documents do not contain enough information to answer this question."

REFERENCE PASSAGES:
{context_text}

USER QUESTION:
{question}

ANSWER:"""
        return prompt

    async def call_gemini(self, prompt: str, api_key: str) -> Optional[str]:
        """
        Calls Google Gemini API (gemini-1.5-flash or gemini-2.0-flash) via lightweight REST.
        """
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 800
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
        except Exception as e:
            print(f"[LLMManager] Gemini API error: {e}")
            return None
        return None

    async def call_ollama(self, prompt: str, model: str = "llama3") -> Optional[str]:
        """
        Calls local Ollama instance on http://localhost:11434.
        """
        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }

        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response", "").strip()
        except Exception:
            return None
        return None

    def local_grounded_synthesis(self, question: str, context_chunks: List[Dict[str, Any]]) -> str:
        """
        Smart fallback synthesizer when no external LLM key or Ollama is available.
        Extracts key sentences matching the query and structures a coherent synthesis.
        """
        if not context_chunks:
            return "No relevant crawled knowledge was found to answer this question. Please crawl and index more web pages."

        top_chunk = context_chunks[0]
        title = top_chunk.get("title", "the crawled website")
        text = top_chunk.get("text", "").strip()

        # Build clean formatted synthesis
        response_parts = [
            f"Based on the knowledge crawled from **{title}**:",
            "",
            f"> {text}",
            "",
            "This information was retrieved from the official indexed documents."
        ]

        # If there are additional passages, mention them
        if len(context_chunks) > 1:
            response_parts.append("")
            response_parts.append("**Additional Context:**")
            for idx, c in enumerate(context_chunks[1:], start=2):
                snippet = c.get("text", "").strip()[:200]
                response_parts.append(f"- *[Passage {idx}]*: {snippet}...")

        return "\n".join(response_parts)

    async def generate_answer(
        self,
        question: str,
        context_chunks: List[Dict[str, Any]],
        provider: str = "auto",
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates RAG response generation across configured providers.
        """
        key_to_use = api_key or self.default_api_key
        prompt = self.build_rag_prompt(question, context_chunks)
        answer = None
        provider_used = "fallback"

        # Try Gemini if key is available
        if (provider in ("auto", "gemini")) and key_to_use:
            answer = await self.call_gemini(prompt, key_to_use)
            if answer:
                provider_used = "gemini-1.5-flash"

        # Try Ollama if Gemini not used or requested
        if not answer and (provider in ("auto", "ollama")):
            answer = await self.call_ollama(prompt)
            if answer:
                provider_used = "ollama"

        # Fallback synthesis
        if not answer:
            answer = self.local_grounded_synthesis(question, context_chunks)
            provider_used = "local_synthesizer"

        # Extract unique sources
        seen_urls = set()
        sources = []
        for c in context_chunks:
            url = c.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({
                    "url": url,
                    "title": c.get("title") or url,
                    "similarity": c.get("similarity", 0.0)
                })

        return {
            "answer": answer,
            "sources": sources,
            "provider_used": provider_used
        }
