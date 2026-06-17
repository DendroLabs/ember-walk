# Lessons Learned

## 2026-06-17: duckduckgo-search package renamed to ddgs

**Problem:** `from duckduckgo_search import DDGS` threw a RuntimeWarning and returned 0 results.

**Misleading symptoms:** The import didn't fail outright — it printed a warning and silently returned empty results, making it look like a search API issue rather than a package issue.

**Root cause:** The `duckduckgo-search` PyPI package was renamed to `ddgs`. The old package still installs but is a deprecation shim that doesn't work properly.

**Fix:** `pip install ddgs` and change import to `from ddgs import DDGS`. The API is also slightly different — no more context manager (`with DDGS() as ddgs:`), just call `DDGS().text()` directly.

**How to avoid:** When a search returns 0 results unexpectedly, check stderr for package deprecation warnings before debugging the query or API.

## 2026-06-17: pip on macOS requires venv

**Problem:** `pip install` and `pip3 install` both refuse to install packages on macOS Sequoia due to PEP 668 (externally managed environment).

**Fix:** Always use a venv: `python3 -m venv .venv && source .venv/bin/activate && pip install ...`. The MCP config must point to the venv's Python binary (`/path/to/.venv/bin/python3`), not the system Python.
