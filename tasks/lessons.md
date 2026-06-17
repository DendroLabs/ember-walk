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

## 2026-06-17: playwright-stealth API changed in v2.x

**Problem:** `from playwright_stealth import stealth_sync` throws `ImportError`.

**Root cause:** playwright-stealth 2.x changed the API. The old `stealth_sync(page)` is now `Stealth().apply_stealth_sync(page)`.

**Fix:** `from playwright_stealth import Stealth` then `Stealth().apply_stealth_sync(page)`.

## 2026-06-17: Trafilatura truncates long technical docs

**Problem:** On Cisco IOS-XR CLI reference pages (100+ command entries per page), trafilatura returned only the first command (~1.5K chars) out of 400K+ chars of actual content.

**Root cause:** Trafilatura is designed for article/blog extraction. Its "main content" heuristic sees repeated command blocks as boilerplate and stops after the first one.

**Fix:** 3-tier extraction pipeline. After trafilatura, check if it captured >15% of the page's visible text. If not, fall back to BeautifulSoup container extraction (find `[role="main"]`, `<main>`, `#content`, etc.) which preserves everything inside the content container. This took extraction from 1.5K to 382K chars on the test page.

**Key insight:** When picking a container, select the **largest** matching container, not the first match. `<article>` tags often match individual subsections (5K chars) while `[role="main"]` matches the full page (420K chars). Check all selectors, keep the biggest.

## 2026-06-17: Moving a venv breaks pip

**Problem:** After moving the project from `~/Documents/misc/webresearch/` to `~/Documents/emberwalk/`, pip inside the venv failed with "bad interpreter" because shebangs in `.venv/bin/` are hardcoded absolute paths.

**Fix:** Recreate the venv at the new location. Venvs are not portable — they embed the path where they were created.
