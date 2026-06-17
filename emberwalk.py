#!/usr/bin/env python3
"""Emberwalk — lightweight web research. Searches, fetches, cleans, outputs markdown."""

import argparse
import os
import re
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

import requests
import trafilatura
import html2text
from ddgs import DDGS

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PLAYWRIGHT_MIN_CONTENT_LEN = 200


# -- Search ------------------------------------------------------------------

def search_duckduckgo(query, max_results=10):
    results = []
    for r in DDGS().text(query, max_results=max_results):
        results.append({"url": r["href"], "title": r["title"], "snippet": r["body"]})
    return results


def search_brave(query, max_results=10):
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        print("BRAVE_API_KEY not set, falling back to DuckDuckGo", file=sys.stderr)
        return search_duckduckgo(query, max_results)
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        params={"q": query, "count": min(max_results, 20)},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for r in data.get("web", {}).get("results", [])[:max_results]:
        results.append({"url": r["url"], "title": r.get("title", ""), "snippet": r.get("description", "")})
    return results


def search(query, max_results=10, use_brave=False):
    fn = search_brave if use_brave else search_duckduckgo
    print(f"Searching: {query} (engine={'brave' if use_brave else 'duckduckgo'})")
    results = fn(query, max_results)
    print(f"Found {len(results)} results")
    return results


# -- Fetch -------------------------------------------------------------------

def make_session():
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def fetch_simple(url, session):
    session.headers["User-Agent"] = random.choice(USER_AGENTS)
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  Simple fetch failed for {url}: {e}", file=sys.stderr)
        return None


def fetch_playwright(url):
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except ImportError:
        print("  Playwright not installed, skipping browser fallback", file=sys.stderr)
        return None

    print(f"  Falling back to Playwright for {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            stealth_sync(page)
            page.goto(url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  Playwright fetch failed for {url}: {e}", file=sys.stderr)
        return None


def fetch_page(url, session, delay=1.5):
    html = fetch_simple(url, session)
    text = trafilatura.extract(html) if html else None
    if not text or len(text.strip()) < PLAYWRIGHT_MIN_CONTENT_LEN:
        html_pw = fetch_playwright(url)
        if html_pw:
            html = html_pw
    if delay > 0:
        time.sleep(delay)
    return html


# -- Clean -------------------------------------------------------------------

def clean_html(html, url):
    if not html:
        return None
    text = trafilatura.extract(html, output_format="txt", include_links=True, include_tables=True)
    if text and len(text.strip()) >= 100:
        return text
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    fallback = h.handle(html)
    if fallback and len(fallback.strip()) >= 100:
        return fallback
    return None


# -- Output ------------------------------------------------------------------

def slugify(text, max_len=60):
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:max_len].rstrip('-')


def write_output(query, results, contents, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index_lines = [
        f"# Research: {query}",
        f"Date: {now}",
        f"Results: {len([c for c in contents if c])}/{len(results)} pages fetched successfully",
        "",
        "---",
        "",
    ]

    file_count = 0
    for i, (result, content) in enumerate(zip(results, contents)):
        if not content:
            index_lines.append(f"- **{result['title']}** — FAILED TO FETCH")
            index_lines.append(f"  {result['url']}")
            index_lines.append("")
            continue

        file_count += 1
        title_slug = slugify(result["title"], max_len=40)
        filename = f"{file_count:02d}_{title_slug}.md"

        page_md = "\n".join([
            f"# {result['title']}",
            f"URL: {result['url']}",
            f"Fetched: {now}",
            "",
            "---",
            "",
            content,
        ])
        (output_dir / filename).write_text(page_md, encoding="utf-8")

        index_lines.append(f"- **[{result['title']}]({filename})**")
        index_lines.append(f"  {result['snippet']}")
        index_lines.append("")

    index_path = output_dir / "index.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")
    print(f"\nOutput written to {output_dir}/")
    print(f"  {file_count} page files + index.md")
    return str(index_path)


# -- CLI ---------------------------------------------------------------------

def run_cli(args):
    results = search(args.query, max_results=args.results, use_brave=args.brave)
    if not results:
        print("No search results found.", file=sys.stderr)
        sys.exit(1)

    session = make_session()
    contents = []
    for i, r in enumerate(results):
        print(f"[{i+1}/{len(results)}] Fetching: {r['title'][:60]}")
        html = fetch_page(r["url"], session, delay=args.delay)
        content = clean_html(html, r["url"])
        contents.append(content)
        if content:
            print(f"  OK ({len(content)} chars)")
        else:
            print("  FAILED — no usable content")

    output_dir = args.output or str(Path("research_output") / slugify(args.query))
    write_output(args.query, results, contents, output_dir)


# -- MCP Server --------------------------------------------------------------

def run_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "emberwalk",
        instructions="Emberwalk — lightweight web research. Searches the web, fetches pages, extracts clean markdown.",
    )

    @mcp.tool()
    def emberwalk(query: str, max_results: int = 10, use_brave: bool = False, output_dir: str = "") -> str:
        """Search the web for a query, fetch and clean the top results, and save as markdown files.

        Args:
            query: The search query
            max_results: Number of pages to fetch (default 10)
            use_brave: Use Brave Search instead of DuckDuckGo (requires BRAVE_API_KEY env var)
            output_dir: Where to save output (default: research_output/{query-slug}/)

        Returns:
            The content of the generated index.md file
        """
        results = search(query, max_results=max_results, use_brave=use_brave)
        if not results:
            return "No search results found."

        session = make_session()
        contents = []
        for i, r in enumerate(results):
            html = fetch_page(r["url"], session, delay=1.5)
            content = clean_html(html, r["url"])
            contents.append(content)

        if not output_dir:
            output_dir = str(Path("research_output") / slugify(query))

        index_path = write_output(query, results, contents, output_dir)
        return Path(index_path).read_text(encoding="utf-8")

    mcp.run(transport="stdio")


# -- Entry point -------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Emberwalk — lightweight web research")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--results", type=int, default=10, help="Number of pages to fetch (default: 10)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests in seconds (default: 1.5)")
    parser.add_argument("--output", help="Output directory (default: research_output/{query-slug}/)")
    parser.add_argument("--brave", action="store_true", help="Use Brave Search (requires BRAVE_API_KEY)")
    parser.add_argument("--serve", action="store_true", help="Run as MCP server")
    args = parser.parse_args()

    if args.serve:
        run_mcp_server()
    elif args.query:
        run_cli(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
