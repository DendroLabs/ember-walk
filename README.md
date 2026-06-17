# Emberwalk

Lightweight web research for LLMs. Searches the web, fetches pages, extracts clean markdown.

Emberwalk exists because tools like [Firecrawl](https://firecrawl.dev) -- which is excellent -- ship with a full Docker stack (Node + Chromium + Redis + job workers) designed for crawling thousands of pages at enterprise scale. If your workflow is "search a topic, read 10 pages, hand the results to an LLM," that's a lot of infrastructure for a simple job.

Emberwalk is a single Python script that does the 80-90% case: search, fetch, clean, output markdown. No Docker, no Redis, no server process.

## How It Works

```
Search --> Fetch --> Clean --> Output
```

1. **Search** -- DuckDuckGo (default) or Brave Search API
2. **Fetch** -- Simple HTTP with realistic headers; auto-falls back to Playwright for JS-heavy pages
3. **Clean** -- Trafilatura extracts main content, strips nav/ads/footers
4. **Output** -- One markdown file per page + an index, ready for LLM consumption

## Install

```bash
cd ~/Documents/emberwalk
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
- `trafilatura` -- content extraction
- `html2text` -- fallback content cleaner
- `playwright` + `playwright-stealth` -- JS rendering (lazy-loaded, only when needed)
- `mcp` -- MCP server mode

## License

MIT
