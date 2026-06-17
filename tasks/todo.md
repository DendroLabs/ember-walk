# Emberwalk - Tasks

## Completed
- [x] Core script (search, fetch, clean, output) — emberwalk.py
- [x] Tiered fetching (simple HTTP -> Playwright fallback)
- [x] Anti-bot defaults (UA rotation, referer, cookies, rate limiting)
- [x] DuckDuckGo + Brave Search support
- [x] MCP server mode (FastMCP + stdio)
- [x] MCP config in ~/.claude/.mcp.json
- [x] Permission in ~/.claude/settings.json
- [x] CLI tested with live query (5/5 pages fetched successfully)
- [x] MCP server initialize handshake verified
- [x] README written
- [x] Git repo initialized, files staged

## Remaining
- [ ] Initial git commit
- [ ] Test MCP tool from within a Claude Code session (restart required to load new server)
- [ ] Add to GitHub repo (user is setting this up)
- [ ] Consider adding: progress output in MCP mode (currently silent during fetch)
- [ ] Consider adding: --json flag for structured output alongside markdown
- [ ] Consider adding: page caching to avoid re-fetching the same URL across runs
