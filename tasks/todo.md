# Emberwalk - Tasks

## Completed
- [x] Core script (search, fetch, clean, output) — emberwalk.py
- [x] Tiered fetching (simple HTTP -> Playwright fallback)
- [x] Anti-bot defaults (UA rotation, referer, cookies, rate limiting)
- [x] DuckDuckGo + Brave Search support
- [x] MCP server mode (FastMCP + stdio)
- [x] MCP config in ~/.claude/.mcp.json
- [x] Permission in ~/.claude/settings.json (mcp__emberwalk__*)
- [x] CLI tested with live query (5/5 pages fetched successfully)
- [x] MCP server handshake verified (all 3 tools listed)
- [x] README written
- [x] Git repo at DendroLabs/ember-walk, 3 commits pushed
- [x] install.sh — one-command setup for new machines
- [x] 3-tier extraction pipeline (trafilatura -> container -> full page)
- [x] Error page detection (403, 404, login walls)
- [x] Cookie consent auto-dismiss
- [x] Lazy-load scroll trigger
- [x] Benchmark vs Firecrawl on Cisco IOS-XR docs (11 pages): EW 6x faster, 56% more content
- [x] Split MCP into ew_search + ew_fetch + ew_research (two-step workflow)

## Remaining
- [ ] Test MCP tools live from a Claude Code session (restart required to load server)
- [ ] Test on work machine (clone + install.sh)
- [ ] Consider: page caching to avoid re-fetching same URL across runs
- [ ] Consider: progress output in MCP mode (currently silent during fetch)
- [ ] Consider: --urls CLI flag for direct URL fetching without search
