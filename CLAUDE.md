# Emberwalk

Single-file Python tool: `emberwalk.py` handles both CLI and MCP server modes.

## Running

- CLI: `python3 emberwalk.py "query" --results 10`
- MCP: `python3 emberwalk.py --serve` (stdio transport)
- Install: `./install.sh` (creates venv, installs deps, Playwright chromium)
- Venv at `.venv/` — MCP config points to `.venv/bin/python3`

## MCP Tools

- `ew_search(query, max_results=20)` — returns JSON snippets, no fetching
- `ew_fetch(urls)` — fetches specific URLs, writes markdown files
- `ew_research(query)` — combined search+fetch (one-shot convenience)

Preferred workflow: ew_search -> LLM picks URLs -> ew_fetch

## Dependencies

- `ddgs` (NOT `duckduckgo-search` — the old name is deprecated and silently breaks)
- `beautifulsoup4` + `lxml` for container extraction
- Playwright + chromium browser installed via `playwright install chromium`
- `playwright-stealth` v2.x — API is `Stealth().apply_stealth_sync(page)`, NOT the old `stealth_sync(page)`

## Architecture

Fetch is tiered: simple HTTP first, Playwright only if content < 200 chars or 403.

Extraction is 3-tier:
1. Trafilatura — fast, clean, works for articles/blogs
2. BeautifulSoup container extraction — finds largest [role="main"]/main/#content/etc
3. Full page with nav/header/footer stripped — last resort

Key: container extraction picks the LARGEST matching selector, not the first. `<article>` often matches a subsection; `[role="main"]` matches the full page.

## Formatting

All tables use basic ASCII characters (+, -, |) only.
