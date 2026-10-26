import os
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

def set_cell_background(cell, hex_color):
    """Sets background color of a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tc_pr.append(shd)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    """Sets internal padding/margins for a cell in dxa (1 pt = 20 dxa)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tc_pr.append(tc_mar)

def add_callout_box(doc, title, text, bg_color="F0FDFA", border_color="0D9488"):
    """Adds a callout box with a colored left border and soft background."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    cell = tbl.cell(0, 0)
    cell.width = Inches(6.5)
    set_cell_background(cell, bg_color)
    set_cell_margins(cell, top=140, bottom=140, left=200, right=180)
    
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:left w:val="single" w:sz="36" w:space="0" w:color="{border_color}"/>'
        f'<w:top w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'<w:bottom w:val="none"/>'
        f'</w:tcBorders>'
    )
    tc_pr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.15
    run_t = p.add_run(f"📌 {title}\n")
    run_t.bold = True
    run_t.font.name = "Calibri"
    run_t.font.size = Pt(11)
    run_t.font.color.rgb = RGBColor(15, 23, 42)
    
    run_b = p.add_run(text)
    run_b.font.name = "Calibri"
    run_b.font.size = Pt(10)
    run_b.font.color.rgb = RGBColor(51, 65, 85)
    
    doc.add_paragraph()

def style_table_header(row, col_widths, hex_bg="0D9488"):
    for idx, cell in enumerate(row.cells):
        cell.width = col_widths[idx]
        set_cell_background(cell, hex_bg)
        set_cell_margins(cell, top=120, bottom=120, left=140, right=140)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.bold = True
                run.font.name = "Calibri"
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(255, 255, 255)

def format_table_rows(table, col_widths, alt_bg="F8FAFC"):
    for r_idx, row in enumerate(table.rows[1:]):
        bg = alt_bg if r_idx % 2 == 1 else "FFFFFF"
        for c_idx, cell in enumerate(row.cells):
            cell.width = col_widths[c_idx]
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for p in cell.paragraphs:
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(2)
                for run in p.runs:
                    run.font.name = "Calibri"
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(30, 41, 59)

def create_report():
    doc = Document()
    
    # Page setup - Margins 0.8 inch
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)
        
    primary_color = RGBColor(13, 148, 136)   # Teal-600
    dark_neutral = RGBColor(15, 23, 42)     # Slate-900
    mid_neutral = RGBColor(71, 85, 105)     # Slate-600
    
    # Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    run_title = p_title.add_run("SIMPLESCRAPER: DEEP REVERSE ENGINEERING ANALYSIS (REA)")
    run_title.bold = True
    run_title.font.name = "Calibri"
    run_title.font.size = Pt(22)
    run_title.font.color.rgb = primary_color
    
    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(14)
    run_sub = p_sub.add_run("Complete Technical Architecture, Internal Engines, Feature Breakdown & Source Code Deconstruction\nTarget Extension ID: lnddbhdmiciimpkbilgpklcglkdegdkg | Version: 3.0.1")
    run_sub.font.name = "Calibri"
    run_sub.font.size = Pt(11)
    run_sub.font.color.rgb = mid_neutral
    
    add_callout_box(
        doc,
        "Executive Summary - Reverse Engineering Analysis (REA)",
        "Yeh report Simplescraper 3.0 (Chrome Extension ID: lnddbhdmiciimpkbilgpklcglkdegdkg) ke unpacked source code, Manifest V3 architecture, background service workers, injected content scripts, AI modules, aur backend Firebase APIs ka complete reverse engineering analysis prastut karti hai. Isme visual list detection, smart field extraction, AI renaming/enrichment, pagination heuristics, aur cloud scraper engines ka deep technical breakdown shaamil hai.",
        bg_color="F0FDFA",
        border_color="0D9488"
    )
    
    # Section 1: Overview & Metadata
    h1 = doc.add_heading(level=1)
    r_h1 = h1.add_run("1. Extension Identity & Manifest V3 Overview")
    r_h1.font.color.rgb = primary_color
    r_h1.bold = True
    
    p = doc.add_paragraph(
        "Simplescraper version 3.0.1 ek modern Google Chrome Extension hai jo web scraping ko browser side panel me AI ke sath seamlessly execute karta hai. Niche iski manifest aur core configuration di gayi hai:"
    )
    p.paragraph_format.line_spacing = 1.15
    
    table1 = doc.add_table(rows=1, cols=3)
    table1.alignment = WD_TABLE_ALIGNMENT.CENTER
    table1.autofit = False
    widths1 = [Inches(1.8), Inches(2.2), Inches(2.5)]
    
    hdr = table1.rows[0]
    hdr.cells[0].paragraphs[0].add_run("Attribute")
    hdr.cells[1].paragraphs[0].add_run("Manifest Configuration")
    hdr.cells[2].paragraphs[0].add_run("Technical Purpose / Role")
    style_table_header(hdr, widths1, "0D9488")
    
    data1 = [
        ("Extension ID", "lnddbhdmiciimpkbilgpklcglkdegdkg", "Chrome Web Store official unique identifier"),
        ("Manifest Version", "Version 3 (Min Chrome 116)", "MV3 compliant; requires Chrome 116+ for SidePanel API"),
        ("Service Worker", "service-worker-loader.js (module)", "Coordinates background messaging, ports, CDP, and settlements"),
        ("Side Panel", "src/sidepanel/sidepanel.html", "Vue 3 based native side panel UI replacing popups"),
        ("Permissions", "storage, activeTab, scripting, tabs, sidePanel, offscreen", "Full browser automation & local storage state management"),
        ("Optional Perms", "cookies, debugger (<all_urls>)", "Debugger for CDP network sniffing; cookies for session sync"),
        ("Offscreen Auth", "src/offscreen/auth.html (IFRAME_SCRIPTING)", "Executes Firebase Google OAuth without blocking Service Worker"),
        ("Externally Connectable", "https://*.simplescraper.io/*", "Allows cloud web dashboard to communicate with local extension")
    ]
    
    for row_data in data1:
        row = table1.add_row()
        for idx, text in enumerate(row_data):
            row.cells[idx].paragraphs[0].add_run(text)
    format_table_rows(table1, widths1)
    doc.add_paragraph()
    
    # Section 2: Full Technology Stack
    h2 = doc.add_heading(level=1)
    r_h2 = h2.add_run("2. Comprehensive Technology Stack (Deconstructed)")
    r_h2.font.color.rgb = primary_color
    r_h2.bold = True
    
    p = doc.add_paragraph(
        "Source code analysis se reveal hota hai ki Simplescraper hybrid client-cloud architecture par chalne wala sophisticated system hai:"
    )
    p.paragraph_format.line_spacing = 1.15
    
    table2 = doc.add_table(rows=1, cols=3)
    table2.alignment = WD_TABLE_ALIGNMENT.CENTER
    table2.autofit = False
    widths2 = [Inches(1.5), Inches(2.3), Inches(2.7)]
    
    hdr2 = table2.rows[0]
    hdr2.cells[0].paragraphs[0].add_run("Layer")
    hdr2.cells[1].paragraphs[0].add_run("Technologies Used")
    hdr2.cells[2].paragraphs[0].add_run("Under-the-Hood Role")
    style_table_header(hdr2, widths2, "0D9488")
    
    data2 = [
        ("Extension UI", "Vue.js 3, Vite, TailwindCSS, Inter Font", "Side panel UI (PanelRoot.js) with reactive forms and tabs"),
        ("Content Scripts", "Vanilla ES6+, Web APIs, Custom Bundler", "Injected directly into host webpage for DOM inspection"),
        ("Selector Engine", "@medv/finder (finder-inline)", "Generates resilient, short, deterministic CSS selectors"),
        ("Network Sniffer", "Chrome DevTools Protocol (CDP v1.3)", "Intercepts raw JSON responses via chrome.debugger"),
        ("State & Storage", "chrome.storage.session & local", "Ephemeral run leases, recipe draft store, credit settlements"),
        ("Auth Layer", "Firebase Auth, Offscreen Document", "Google OAuth SSO using iframe scripted offscreen context"),
        ("Database / Cloud", "Google Cloud Firestore, Firebase Hosting", "Stores user credits, saved recipes, and run results"),
        ("AI Cloud Functions", "Firebase Callable Functions, Gemini/OpenAI", "aiRenameProperties, aiRecommendPagination, aiPanelRequest"),
        ("Cloud Runners", "Node.js, Puppeteer/Playwright, Proxies", "Runs scheduled headless jobs in Docker containers"),
        ("AI Agent Protocol", "Model Context Protocol (MCP)", "https://mcp.simplescraper.io/mcp for Claude Desktop & Cursor")
    ]
    
    for row_data in data2:
        row = table2.add_row()
        for idx, text in enumerate(row_data):
            row.cells[idx].paragraphs[0].add_run(text)
    format_table_rows(table2, widths2)
    doc.add_paragraph()
    
    # Section 3: Core Internal Modules & Engines
    h3 = doc.add_heading(level=1)
    r_h3 = h3.add_run("3. In-Depth Feature Engineering & How Each Engine Works")
    r_h3.font.color.rgb = primary_color
    r_h3.bold = True
    
    modules = [
        ("A. __ListDetector (Automated Visual List Detection)",
         "ListDetector module web page ke pure DOM tree ko traverse karta hai aur repeating elements (cards, rows, grid items, articles) ko pehchanta hai.\n"
         "• Heuristic Tree Traversal: Siblings jinka tag, class ya structural depth similar ho unhe group karta hai.\n"
         "• Scoring Algorithm: Children count, inner text balance, image presence aur container size ke aadhar par score calculate karta hai.\n"
         "• Optimal Selection: Page ka best candidate container select hota hai aur user ko highlight karke 'Start Scraping' me dikhaya jata hai."),
        
        ("B. __FieldExtractor (v3.18 Smart Attribute Extraction)",
         "FieldExtractor module list ke andar individual columns aur properties extract karta hai:\n"
         "• Attribute Detection: Automatically 'href' (for links), 'src' (for images), and innerText (for content) detect karta hai.\n"
         "• 6 Content Cleaning Filters:\n"
         "  1. singleNonAlphanumeric: Decorative characters (•, ·, →, |) ko drop karta hai.\n"
         "  2. whitespaceOnly: Khali spaces aur line-breaks ko clean karta hai.\n"
         "  3. tooShort: 2 character se chote invalid strings ko drop karta hai.\n"
         "  4. htmlEntities: Common entities (&nbsp;, &bull;, &middot;) ko sanitize karta hai.\n"
         "  5. repeatedSingleChar: Repeated chars (..., ---, ___) ko filter karta hai.\n"
         "  6. ancestorInlineTextCaptured: Duplicate nested text extraction ko block karta hai agar parent pehle hi text capture kar chuka ho."),
        
        ("C. AI Property Renaming (aiRenameProperties Callable)",
         "User ko cryptic class names (jaise 'css-189fka' ya 'sc-2x3y') ki jagah clean column names provide karne ke liye AI renaming kaam karta hai:\n"
         "• DOM Extraction: Har field ka sample text (up to 20 rows) aur attribute type (href/src/text) collect hota hai.\n"
         "• Cloud Function Dispatch: Extension backend Firebase callable function 'aiRenameProperties' ko sample JSON bhejta hai.\n"
         "• Semantic Output: LLM context analyze karke automatically 'product_title', 'discounted_price', 'rating_score', 'product_url' jaise columns assign karta hai."),
        
        ("D. AI Fields & Enrichment (fillAiField & previewAiField)",
         "Scraped data ko real-time me AI se transform karne ka engine:\n"
         "• Supported AI Tasks: Summarize (lambe text ko summarize karna), Classify (sentiment ya category nikalna), Extract (emails, specs, dates), aur Boolean Questions ('In stock?', 'Is hiring?').\n"
         "• Batching & Concurrency: Data rows ko batches me split karke cloud LLM par asynchronously bheja jata hai.\n"
         "• Progress Callback: Real-time progress bar side panel me dikhta hai (done/total rows)."),
        
        ("E. Intelligent Pagination Detector (__PaginationDetector)",
         "Multi-page scraping ke liye AI aur heuristic-based pagination detector:\n"
         "• Element Candidate Matching: 'a, button, [role=\"button\"], [role=\"link\"]' ko inspect karta hai.\n"
         "• Heuristic Filters: rel='next' attribute, 'DOCUMENT_POSITION_FOLLOWING' (container ke baad aane wale elements), aur forward direction check karta hai.\n"
         "• Cloud AI Probability: Candidates ko 'aiRecommendPagination' cloud function par bhejta hai jo har candidate ka probability score nikalta hai."),
        
        ("F. Navigation Controller (__SimplescraperNavigationController)",
         "Browser ke andar autonomous pagination execution engine:\n"
         "• State Management: Pages navigated, items collected, visited URLs ko track karta hai.\n"
         "• Duplicate Prevention: Har extracted element par 'data-simplescraper-extracted=\"true\"' attribute laga deta hai taaki dobara scrap na ho.\n"
         "• Resilient Traversal: Single-Page Application (SPA) me bina page reload ke 'Load More' click karta hai ya URL increment parameter ('?page=N') update karta hai."),
        
        ("G. Network Sniffer / CDP API Scraper",
         "Jab DOM scraping muskil hoti hai ya website dynamic JS data load karti hai:\n"
         "• Chrome Debugger API: Extension 'chrome.debugger' attach karta hai (protocol 1.3) aur 'Network.enable' activate karta hai.\n"
         "• JSON Interceptor: Har XHR/Fetch request ka response 'Network.getResponseBody' se intercept karke raw JSON parse karta hai.\n"
         "• Direct JSON Mapping: Bina HTML touch kiye directly internal API se thousands of products scrape kar leta hai."),
        
        ("H. Model Context Protocol (MCP) Server",
         "Modern AI agents (Claude Desktop, Claude Code, Cursor) ke liye remote MCP server:\n"
         "• Endpoint: https://mcp.simplescraper.io/mcp\n"
         "• Features: Natural language instructions se scrape trigger karna, site ka sitemap inspect karna, clean markdown me articles read karna, aur recipes cloud me run karna.")
    ]
    
    for title, desc in modules:
        add_callout_box(doc, title, desc, bg_color="F8FAFC", border_color="0284C7")
        
    # Section 4: Recipes Schema
    h4 = doc.add_heading(level=1)
    r_h4 = h4.add_run("4. Recipe Data Model & JSON Configuration Schema")
    r_h4.font.color.rgb = primary_color
    r_h4.bold = True
    
    p = doc.add_paragraph(
        "Simplescraper me scraping logic ek declarative 'Recipe' object me serialize hota hai jise cloud ya local dono jagah execute kiya ja sakta hai. Niche iska extracted structure hai:"
    )
    p.paragraph_format.line_spacing = 1.15
    
    recipe_code = """{
  "id": "recipe-1729960000-abc123xyz",
  "name": "E-Commerce Product Scraper",
  "type": "visual", // or "network"
  "createdAt": "2026-10-26T22:30:00.000Z",
  "websiteContext": {
    "pageUrl": "https://example.com/products",
    "domain": "example.com",
    "targetRequestUrl": null
  },
  "source": {
    "kind": "list",
    "listId": "list-1",
    "name": "Product Cards",
    "listParentSelector": "div.products-grid",
    "listChildSelector": "div.product-card"
  },
  "fields": [
    {
      "id": "f1",
      "name": "product_name",
      "kind": "text",
      "def": { "selector": "h3.title" },
      "include": true
    },
    {
      "id": "f2",
      "name": "product_price",
      "kind": "text",
      "def": { "selector": "span.price" },
      "include": true
    },
    {
      "id": "f3",
      "name": "product_url",
      "kind": "link",
      "def": { "selector": "a.link" },
      "attribute": "href",
      "include": true
    }
  ],
  "ai_columns": [
    {
      "uid": "ai-col-1",
      "key": "sentiment_analysis",
      "task": "classify",
      "prompt": "Classify sentiment as Positive, Neutral, or Negative",
      "reads": ["f1"]
    }
  ],
  "pagination": {
    "enabled": true,
    "source": "click", // "click", "infinite_scroll", or "incremented"
    "selector": "a.next-page-btn",
    "maxPages": 10,
    "waitBeforeNext": 1500
  },
  "runSettings": {
    "reloadPage": false,
    "waitBeforeCapture": 2000,
    "scrollTuning": { "step": 500, "delay": 200 }
  }
}"""
    p_code = doc.add_paragraph()
    p_code.paragraph_format.space_before = Pt(4)
    p_code.paragraph_format.space_after = Pt(8)
    run_c = p_code.add_run(recipe_code)
    run_c.font.name = "Consolas"
    run_c.font.size = Pt(8.5)
    run_c.font.color.rgb = RGBColor(15, 23, 42)
    
    # Section 5: Step-by-Step Architectural Blueprint
    h5 = doc.add_heading(level=1)
    r_h5 = h5.add_run("5. Architectural Blueprint: How to Build a Simplescraper Clone")
    r_h5.font.color.rgb = primary_color
    r_h5.bold = True
    
    p = doc.add_paragraph(
        "Agar aapko Simplescraper jaisa Fast AI Web Scraper develop karna ho, toh niche diya gaya 4-layer architectural framework implement karein:"
    )
    p.paragraph_format.line_spacing = 1.15
    
    clone_steps = [
        ("Layer 1: SidePanel Extension (Manifest V3)",
         "Chrome 116+ ka sidePanel API use karein. Vue 3 ya React ke sath reactive state maintain karein. Offscreen document create karein Google OAuth handling ke liye."),
        ("Layer 2: Injected Content Script Heuristics",
         "1. List Detection: DOM tree compare karke sibling nodes calculate karein.\n"
         "2. CSS Selector: @medv/finder use karke short resilient selectors banayein.\n"
         "3. Sanitization: RegEx filters se single characters, white spaces, aur HTML entities clean karein.\n"
         "4. Pagination: DOCUMENT_POSITION_FOLLOWING aur rel='next' se next page buttons pehchanein."),
        ("Layer 3: AI Cloud Microservices",
         "Node.js/Python FastAPI ya Firebase Cloud Functions me do main APIs banayein:\n"
         "• /api/ai-rename: Sample text + attribute le kar standard clean column names return kare.\n"
         "• /api/ai-enrich: Extracted rows par LLM prompts (summarize, classify, extract) run kare."),
        ("Layer 4: Cloud Runner & MCP Server",
         "Docker containers me Headless Chromium (Playwright/Puppeteer) run karein. Recipes ko cron schedule par chalayein aur webhooks / Google Sheets API me push karein. Ek MCP server expose karein taaki Claude ya Cursor directly bots execute kar sakein.")
    ]
    
    for title, desc in clone_steps:
        add_callout_box(doc, title, desc, bg_color="F0FDF4", border_color="16A34A")
        
    out_path = r"e:\python projects\axomai_bot\Simplescraper_Complete_Reverse_Engineering_Analysis_Report.docx"
    doc.save(out_path)
    print("Report saved successfully at:", out_path)

if __name__ == "__main__":
    create_report()
