# Emberwalk

Single-file Python tool: `emberwalk.py` handles both CLI and MCP server modes.

## Running

- CLI: `python3 emberwalk.py "query" --results 10`
- MCP: `python3 emberwalk.py --serve` (stdio transport)
- Venv at `.venv/` — MCP config points to `.venv/bin/python3`

## Dependencies

- `ddgs` (NOT `duckduckgo-search` — the old name is deprecated and silently breaks)
- Playwright + chromium browser installed via `playwright install chromium`

## Architecture

Four-stage pipeline: Search -> Fetch -> Clean -> Output. Fetch is tiered: simple HTTP first, Playwright only if content is under 200 chars. Trafilatura for extraction, html2text as fallback.

## Formatting

All tables use basic ASCII characters (+, -, |) only.
