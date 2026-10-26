# Axom AI Web Crawler & Semantic RAG Bot

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=flat&logo=Python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33.svg?style=flat&logo=Playwright&logoColor=white)](https://playwright.dev/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-Vector_Store-FF6F00.svg?style=flat)](https://www.trychroma.com/)
[![MCP](https://img.shields.io/badge/MCP-JSON--RPC_2.0-8A2BE2.svg?style=flat)](https://modelcontextprotocol.io)

An intelligent, production-ready web crawler, text cleaner, and semantic retrieval-augmented generation (RAG) platform tailored for multilingual content with complete Unicode (Assamese, Hindi, English) preservation, persistent URL cataloging, automated XML sitemap discovery, and **Model Context Protocol (MCP)** integration.

---

## 🌟 Core Features

- **🌐 Intelligent Multilingual Extraction**: Preserves Indic scripts (Assamese, Bengali, Hindi) without encoding artifacts using Trafilatura, BeautifulSoup, and custom boilerplate scrubbers.
- **⚡ Playwright Browser Pooling**: Reusable headless Chromium browser instance with isolated contexts per page for fast JS rendering without memory bloat.
- **🗺️ Automated XML Sitemap Discovery**: Probes `/sitemap.xml`, `/sitemap_index.xml`, and `/wp-sitemap.xml` recursively before crawling to immediately queue high-priority canonical pages.
- **🔄 Smart Deduplication & Change Detection**: SHA-256 content hashing detects unchanged pages across recrawls to save bandwidth and compute.
- **🧠 ChromaDB Vector Storage**: Semantic vector search with sentence embeddings for low-latency similarity queries.
- **🤖 Model Context Protocol (MCP) Server**: Native JSON-RPC 2.0 endpoint (`/api/mcp`) supporting Claude Desktop, Cursor, and autonomous AI agents.
- **🛡️ Enterprise Security**: Built-in IP lockout protection, CSRF validation, Django-compatible password hashing, and session cookie authentication.
- **📊 Real-time Dashboard**: Live single-page monitoring dashboard with real-time logs, live page counts, and instant CSV/JSON/PDF exports.

---

## 🛠️ Tech Stack

- **Backend**: FastAPI, Starlette, Uvicorn
- **Crawler**: HTTPX (async), Playwright (Chromium)
- **Content Cleaning**: Trafilatura, BeautifulSoup4, LXML
- **Vector DB / RAG**: ChromaDB, Sentence-Transformers
- **Database / Auth**: PostgreSQL, Psycopg2, ItsDangerous
- **Protocol**: Model Context Protocol (MCP) 2024-11-05

---

## 🚀 Getting Started

### 1. Clone Repository
```bash
git clone https://github.com/Samarjitkashyp/axomai-bot.git
cd axomai-bot
```

### 2. Set Up Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Environment Variables
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

### 4. Run Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```
Dashboard available at `http://127.0.0.1:8001/` and API docs at `http://127.0.0.1:8001/docs`.

---

## 🔌 Model Context Protocol (MCP) Integration

Connect **Claude Desktop** or **Cursor** to Axom AI Bot:

Add to your `claude_desktop_config.json` or Cursor MCP settings:
```json
{
  "mcpServers": {
    "axomai-bot": {
      "url": "https://axomai-bot.aiaxom.co.in/api/mcp",
      "headers": {
        "X-Bot-Token": "YOUR_BOT_IMPORT_TOKEN"
      }
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
| :--- | :--- |
| `crawl_website` | Starts an asynchronous background crawl for a given seed URL. |
| `get_crawl_status` | Returns progress, stats, logs, and recently crawled pages. |
| `search_knowledge_base` | Semantic vector search across all indexed web data. |
| `discover_sitemap` | Probes and extracts URLs from the target site's XML sitemaps. |

---

## 🧪 Testing

Run the test suite:
```bash
python -m unittest discover tests/
```

---

## 📄 License

MIT License. Developed for Axom AI.
