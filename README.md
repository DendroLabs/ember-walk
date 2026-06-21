# Emberwalk

Lightweight web research for LLMs. Searches the web, fetches pages, extracts clean markdown.

Emberwalk exists because tools like [Firecrawl](https://firecrawl.dev) -- which is excellent -- ship with a full Docker stack (Node + Chromium + Redis + job workers) designed for crawling thousands of pages at enterprise scale. If your workflow is "search a topic, read 10 pages, hand the results to an LLM," that's a lot of infrastructure for a simple job.

Emberwalk is a single Python script that does the 80-90% case: search, fetch, clean, output markdown. No Docker, no Redis, no server process.

## How It Works

```
Search --> Fetch --> Clean --> Output
```

1. **Search** -- DuckDuckGo (default) or Brave Search API
2. **Fetch** -- Simple HTTP with realistic headers; auto-falls back to Playwright for JS-heavy or bot-protected pages. PDFs (`.pdf` URLs) are downloaded and text-extracted via `pypdf`
3. **Clean** -- 3-tier extraction pipeline (see below)
4. **Output** -- One markdown file per page + an index, ready for LLM consumption

### Content Extraction (3-tier)

Not all pages are created equal. Blog posts, technical docs, and SPAs all need different extraction strategies. Emberwalk tries them in order and picks the best result:

- **Tier 1: Trafilatura** -- Fast, clean, great for articles and blog posts. If it captures >15% of the page's visible text, we're done.
- **Tier 2: Container extraction** -- Finds the main content container (`<main>`, `[role="main"]`, `#content`, etc.) and converts it to markdown. Handles long technical docs where Trafilatura truncates.
- **Tier 3: Full page** -- Strips nav/header/footer/sidebar elements and converts the rest. Last resort, but never loses content.

Error pages (403, 404, login walls) are detected and skipped rather than saved as garbage. Cookie consent modals are auto-dismissed. Lazy-loaded content is triggered by scrolling before extraction.

## Install

```bash
git clone https://github.com/DendroLabs/ember-walk.git
cd ember-walk
./install.sh
```

The installer creates a virtual environment, installs all Python dependencies, and downloads the Chromium browser for Playwright. It prints the exact paths to use for CLI and MCP configuration when it finishes.

Manual install if you prefer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

### CLI

```bash
# Basic search -- fetches 10 pages, outputs to research_output/{query-slug}/
python3 emberwalk.py "iphone camera filter apps"

# Fewer results, custom output directory
python3 emberwalk.py "best note taking apps" --results 5 --output ./notes-research/

# Use Brave Search (requires BRAVE_API_KEY env var)
python3 emberwalk.py "rust web frameworks" --brave

# Adjust politeness delay between requests (default 1.5s)
python3 emberwalk.py "competitive analysis widgets" --delay 2.0
```

### MCP Server (for Claude Code)

Add to `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "emberwalk": {
      "command": "/path/to/emberwalk/.venv/bin/python3",
      "args": ["/path/to/emberwalk/emberwalk.py", "--serve"]
    }
  }
}
```

Then in Claude Code, the `emberwalk` tool is available:

```
emberwalk(query="iphone camera filter apps", max_results=10)
```

The tool writes markdown files to disk and returns the index, so the LLM can read individual pages as needed.

## Output Structure

```
research_output/iphone-camera-filter-apps/
  index.md                              # Summary + links to each page
  01_best-camera-filter-apps-2026.md    # Individual page content
  02_techcrunch-photo-editing-roundup.md
  03_...
```

Each page file includes:
- Title, source URL, fetch timestamp
- Clean extracted content (no HTML, no boilerplate)

## Prompt Injection Protection

All fetched content passes through `sanitizer.py` before being returned to the LLM. It scans for and redacts:

- **Instruction overrides** -- "ignore all previous instructions", "your new task is..." (CRITICAL)
- **Role hijacking** -- "you are now...", "act as...", DAN mode, developer mode (CRITICAL)
- **Model control tokens** -- `<|im_start|>`, `[INST]`, `<<SYS>>` and similar delimiters (CRITICAL)
- **System prompt extraction** -- "reveal your system prompt", "repeat everything above" (CRITICAL)
- **Indirect injection** -- labelled directives like `[Note to Claude:]`, `ASSISTANT:` (HIGH)
- **Data exfiltration** -- malicious image links, `fetch()`, `sendBeacon()` calls (HIGH)
- **Invisible Unicode** -- zero-width spaces, RTL overrides, tag block characters (MEDIUM)
- **Homoglyphs** -- Cyrillic/Greek characters visually identical to Latin (MEDIUM)
- **Base64 payloads** -- long base64 blobs that decode to injection content (HIGH)

Matches are redacted inline with `[REDACTED: category/severity]` and a plain-English warning is prepended to the page content. Search snippets (`ew_search`) are not sanitized -- they are low-risk and short.

No LLM or external service involved -- pure stdlib regex, runs in-process.

## Anti-Bot Defaults

Built in, no configuration needed:
- Rotating User-Agent strings (Chrome, Firefox, Safari)
- Google referer header
- Cookie persistence across requests
- Polite rate limiting (1.5s default delay)
- Playwright with stealth patches for JS-heavy fallback

## Search Engines

**DuckDuckGo** (default) -- no API key required, no rate limits, good enough for most research.

**Brave Search** -- better result quality, requires a free API key from [brave.com/search/api](https://brave.com/search/api) (2,000 queries/month on free tier). Set `BRAVE_API_KEY` env var and pass `--brave`.

## When to Use Firecrawl Instead

Emberwalk is not a replacement for Firecrawl. Use Firecrawl when you need:

- **Site-wide crawling** with sitemaps, depth control, and link discovery
- **Async job queues** for crawling thousands of pages with concurrency control
- **Webhook callbacks** for long-running crawls
- **Residential proxy rotation** for sites with aggressive bot protection
- **Structured extraction with schemas** backed by an LLM
- **Production SaaS** with uptime guarantees and a managed API

Firecrawl is built for scale. Emberwalk is built for "I need to research something right now."

## Dependencies

- `ddgs` -- DuckDuckGo search
- `requests` -- HTTP fetching
- `trafilatura` -- content extraction (Tier 1)
- `beautifulsoup4` + `lxml` -- DOM parsing and container extraction (Tier 2/3)
- `html2text` -- HTML to markdown conversion
- `playwright` + `playwright-stealth` -- JS rendering (lazy-loaded, only when needed)
- `pypdf` -- PDF text extraction (lazy-loaded; PDFs are skipped gracefully if absent)
- `mcp` -- MCP server mode

## Disclaimer

Emberwalk's sanitizer is designed to catch the passive prompt injection threats that come with web scraping -- the injected payloads embedded in web pages, SEO spam, and user-generated content that try to hijack any LLM that happens to ingest them. It is **not a guarantee** against targeted or novel attacks, and sophisticated payloads may evade pattern-based detection. Emberwalk should be used as one layer alongside other security practices, not as your only line of defense. Use at your own risk.

## License

MIT
