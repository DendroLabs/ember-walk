#!/usr/bin/env python3
"""Emberwalk — lightweight web research. Searches, fetches, cleans, outputs markdown."""

import argparse
import os
import re
import socket
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from sanitizer import sanitize as _sanitize
except ImportError:
    _sanitize = None

import requests
import trafilatura
import html2text
from bs4 import BeautifulSoup
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

MAIN_CONTENT_SELECTORS = [
    "[role='main']",
    "main",
    "#main-content",
    "#content",
    "#chapterContent",        # Cisco docs
    "#fw-content",            # Cisco docs alt
    "#pageContentDiv",        # Cisco docs alt
    "#mw-content-text",       # Wikipedia
    ".doc-content",
    ".documentation-content",
    ".article-content",
    ".post-content",
    ".entry-content",
    ".td-content",
    "article",                # last — often matches a single subsection, not the full page
]

ERROR_SIGNALS = [
    "access denied",
    "403 forbidden",
    "404 not found",
    "page not found",
    "this page isn't available",
    "you don't have permission",
    "sign in to continue",
    "log in to your account",
    "please enable javascript",
]


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


# -- Diagnostics -------------------------------------------------------------

def _check_connectivity(timeout=3.0):
    """Return True if the machine has any internet connectivity.

    Raw TCP connect to well-known hosts (Cloudflare / Google DNS) so we can
    tell 'this whole machine is offline' apart from 'emberwalk's search or
    fetch failed'. Only called on the failure path, so the latency is fine.
    """
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _diagnose_failure(operation, reason):
    """Build a user-facing diagnostic for an emberwalk failure.

    Distinguishes a machine-wide outage from an emberwalk-side problem so the
    user knows whether the issue is their network or the tool itself.
    """
    if not _check_connectivity():
        return (
            f"Emberwalk: {operation} failed, and a direct connectivity check "
            f"also failed -- this machine appears to be offline. Check your "
            f"Wi-Fi / VPN / network connection. (underlying error: {reason})"
        )
    return (
        f"Emberwalk: {operation} failed, but the network is reachable -- this "
        f"is an emberwalk-side problem, not your connection. "
        f"Underlying error: {reason}"
    )


def _all_fetches_failed(contents):
    """True when at least one page was attempted and every one came back empty."""
    return bool(contents) and all(not c for c, _ in contents)


# -- Fetch -------------------------------------------------------------------

def make_session():
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def _request_with_retry(url, session, retries=3):
    """GET a URL with retry/backoff on transient HTTP + connection errors.

    Returns the requests.Response on success, or None on failure. Rotates the
    User-Agent per attempt to dodge per-UA/per-burst throttling. Shared by
    fetch_simple (HTML, uses .text) and fetch_pdf (uses .content).
    """
    RETRYABLE_STATUS = {403, 429, 500, 502, 503}
    for attempt in range(1 + retries):
        session.headers["User-Agent"] = random.choice(USER_AGENTS)
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in RETRYABLE_STATUS and attempt < retries:
                retry_after = (e.response.headers.get("Retry-After")
                               if e.response is not None else None)
                if retry_after and str(retry_after).isdigit():
                    wait = min(float(retry_after), 30.0)
                else:
                    wait = min(1.5 * (2 ** attempt), 20.0) + random.random()
                print(f"  HTTP {status} on {url}, backing off {wait:.1f}s "
                      f"(attempt {attempt + 1}/{retries + 1})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  Simple fetch failed for {url}: {e}", file=sys.stderr)
            return None
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retries:
                wait = 1.0 + random.random()
                print(f"  Retrying {url} in {wait:.1f}s ({e})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  Simple fetch failed for {url}: {e}", file=sys.stderr)
            return None
        except requests.RequestException as e:
            print(f"  Simple fetch failed for {url}: {e}", file=sys.stderr)
            return None


def fetch_simple(url, session, retries=3):
    resp = _request_with_retry(url, session, retries=retries)
    return resp.text if resp is not None else None


def fetch_playwright(url, browser=None):
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError:
        print("  Playwright not installed, skipping browser fallback", file=sys.stderr)
        return None

    print(f"  Browser fetch for {url}")

    def _fetch(browser_instance):
        context = browser_instance.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        Stealth().apply_stealth_sync(page)
        page.goto(url, wait_until="networkidle", timeout=30000)
        _dismiss_cookie_consent(page)
        _scroll_to_bottom(page)
        page.wait_for_timeout(2000)
        html = page.content()
        context.close()
        return html

    try:
        if browser:
            return _fetch(browser)
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            html = _fetch(b)
            b.close()
            return html
    except Exception as e:
        print(f"  Playwright fetch failed for {url}: {e}", file=sys.stderr)
        return None


def _dismiss_cookie_consent(page):
    """Click common cookie consent buttons to reveal content behind modals."""
    consent_selectors = [
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I Agree')",
        "button:has-text('OK')",
        "#onetrust-accept-btn-handler",
        ".cookie-consent-accept",
        "[data-testid='cookie-accept']",
    ]
    for sel in consent_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _scroll_to_bottom(page):
    """Scroll to trigger lazy-loaded content."""
    try:
        page.evaluate("""
            async () => {
                const delay = ms => new Promise(r => setTimeout(r, ms));
                const height = () => document.body.scrollHeight;
                let prev = 0;
                while (height() !== prev) {
                    prev = height();
                    window.scrollTo(0, height());
                    await delay(800);
                }
            }
        """)
    except Exception:
        pass


def fetch_page(url, session, delay=1.5, browser=None):
    html = fetch_simple(url, session)
    text = trafilatura.extract(html) if html else None
    if not text or len(text.strip()) < PLAYWRIGHT_MIN_CONTENT_LEN:
        html_pw = fetch_playwright(url, browser=browser)
        if html_pw:
            html = html_pw
    if delay > 0:
        time.sleep(delay)
    return html


def _looks_like_pdf(url):
    """True if the URL path (ignoring query/fragment) ends in .pdf."""
    path = url.lower().split("?", 1)[0].split("#", 1)[0]
    return path.endswith(".pdf")


def fetch_pdf(url, session, delay=1.5):
    """Download a PDF and extract its text. Returns (content, warning).

    pypdf is an optional dependency — if it's missing, the PDF is skipped with
    a clear message (same pattern as the optional Playwright fallback). Reuses
    _request_with_retry so PDFs get the same 403/backoff/UA-rotation handling.
    """
    try:
        import pypdf
    except ImportError:
        print(f"  Skipping PDF (pypdf not installed): {url}", file=sys.stderr)
        print("  Install with: pip install pypdf", file=sys.stderr)
        return None, None

    print(f"  PDF fetch for {url}")
    resp = _request_with_retry(url, session)
    if resp is None:
        return None, None
    data = resp.content
    if not data.startswith(b"%PDF"):
        print(f"  Not a valid PDF (no %PDF header): {url}", file=sys.stderr)
        return None, None

    import io
    parts = None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        parts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as e:
        print(f"  PDF parse failed for {url}: {e}", file=sys.stderr)
    if delay > 0:
        time.sleep(delay)
    if parts is None:
        return None, None

    text = "\n\n".join(p.strip() for p in parts if p.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    warning = None
    if len(text) < 100:
        warning = (f"possible PDF extraction failure ({len(text)} chars from "
                   f"{len(parts)} pages — may be scanned/image-only)")
        print(f"  WARNING: {warning}", file=sys.stderr)
        if not text:
            return None, warning

    return _apply_sanitizer(text), warning


def fetch_and_extract(url, session, delay=1.5, browser=None):
    """Fetch a URL and return (content, warning). Routes PDFs to fetch_pdf."""
    if _looks_like_pdf(url):
        return fetch_pdf(url, session, delay=delay)
    html = fetch_page(url, session, delay=delay, browser=browser)
    return clean_html(html, url)


# -- Clean -------------------------------------------------------------------

def _visible_text_len(html):
    """Rough char count of all visible text in the HTML."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()
    return len(soup.get_text(separator=" ", strip=True))


def _detect_error_page(html):
    """Return a reason string if this looks like an error/login/block page."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ", strip=True).lower()[:2000]
    if len(text) < 50:
        return "empty page"
    for signal in ERROR_SIGNALS:
        if signal in text:
            return signal
    return None


def _ensure_inter_element_spaces(soup):
    """Insert whitespace between adjacent inline elements that would merge.

    html2text and get_text(strip=True) drop whitespace at element boundaries,
    causing 'RoutersRouting' / 'ersModular' artifacts. This inserts a space
    NavigableString between adjacent inline tags where none exists.
    """
    from bs4 import NavigableString
    inline_tags = {
        "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em",
        "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small",
        "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
    }
    for tag in soup.find_all(True):
        if tag.name not in inline_tags:
            continue
        nxt = tag.next_sibling
        if nxt is None:
            continue
        if isinstance(nxt, NavigableString):
            # If the text node between elements is empty/whitespace-only, ensure
            # at least one space so words don't merge
            if nxt.string is not None and nxt.string.strip() == '' and len(nxt.string) == 0:
                nxt.replace_with(' ')
        elif hasattr(nxt, 'name') and nxt.name in inline_tags:
            tag.insert_after(NavigableString(' '))


def _extract_admonitions_from_tables(soup):
    """Move Note/Warning/Caution boxes out of table cells.

    Cisco docs embed admonition divs inside <td> cells, which html2text
    renders as extra columns in the same row — breaking the table's column
    count. This extracts them and places them after the parent table.
    """
    admonition_selectors = [
        "[class*='note']", "[class*='Note']",
        "[class*='warning']", "[class*='Warning']",
        "[class*='caution']", "[class*='Caution']",
        "[class*='alert']",
    ]
    admonition_text_signals = ["note", "caution", "warning", "tip"]

    for table in list(soup.find_all("table")):
        extracted = []
        for cell in table.find_all(["td", "th"]):
            # Find admonitions by class
            for sel in admonition_selectors:
                for adm in cell.select(sel):
                    extracted.append(adm.extract())
            # Find bold Note/Warning/Caution markers that start a block
            for bold in cell.find_all(["b", "strong"]):
                text = (bold.get_text(strip=True) or "").lower().rstrip(":")
                if text in admonition_text_signals:
                    # Extract the bold tag and all following siblings in this cell
                    adm_parts = [bold.extract()]
                    while bold.next_sibling:
                        adm_parts.append(bold.next_sibling.extract())
                    wrapper = soup.new_tag("div")
                    wrapper["class"] = ["extracted-admonition"]
                    for part in adm_parts:
                        wrapper.append(part)
                    extracted.append(wrapper)
                    break
        # Place extracted admonitions after the table
        insert_point = table
        for adm in extracted:
            insert_point.insert_after(adm)
            insert_point = adm


def _normalize_gfm_tables(md):
    """Rewrite html2text's loose pipe tables into valid GitHub-Flavored Markdown.

    Anchors on separator lines and rewrites adjacent header/data rows with
    proper leading/trailing pipes and column-matched separators. Only touches
    lines adjacent to a separator — prose with inline | is left alone.
    """
    sep_re = re.compile(r'^\s*\|?\s*:?-{2,}:?(\s*\|\s*:?-{2,}:?)+\s*\|?\s*$')

    def cells(row):
        r = row.strip()
        if r.startswith("|"):
            r = r[1:]
        if r.endswith("|"):
            r = r[:-1]
        return [c.strip() for c in r.split("|")]

    def fmt(cell_list, ncol):
        cl = list(cell_list)
        if len(cl) < ncol:
            cl += [""] * (ncol - len(cl))
        elif len(cl) > ncol:
            cl = cl[:ncol - 1] + [" ".join(cl[ncol - 1:])]
        return "| " + " | ".join(cl) + " |"

    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        if sep_re.match(lines[i]):
            ncol = max(2, len(cells(lines[i])))
            hidx = len(out) - 1
            while hidx >= 0 and out[hidx].strip() == "":
                hidx -= 1
            header = out[hidx] if (hidx >= 0 and "|" in out[hidx]) else None
            j = i + 1
            rows = []
            while j < n and lines[j].strip() != "" and "|" in lines[j]:
                rows.append(lines[j])
                j += 1
            header_cells = cells(header) if header is not None else []
            header_has_text = any(header_cells)
            rows_have_text = any(any(c for c in cells(r)) for r in rows)
            if not header_has_text and not rows_have_text:
                if header is not None:
                    del out[hidx]
                i = j
                continue
            if header is not None:
                out[hidx] = fmt(header_cells, ncol)
            else:
                out.append(fmt([""] * ncol, ncol))
            out.append("| " + " | ".join(["---"] * ncol) + " |")
            for r in rows:
                rc = cells(r)
                if not any(rc):
                    continue
                out.append(fmt(rc, ncol))
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _soup_to_markdown(soup):
    """Convert a BeautifulSoup tree to markdown with pre-processing."""
    _ensure_inter_element_spaces(soup)
    _extract_admonitions_from_tables(soup)
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    return _normalize_gfm_tables(h.handle(str(soup)))


def _extract_main_container(html):
    """Find the main content container and convert to markdown.

    Picks the largest qualifying container to avoid grabbing a single
    <article> subsection when a parent container holds the full page.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    best = None
    best_len = 0
    for sel in MAIN_CONTENT_SELECTORS:
        for container in soup.select(sel):
            text_len = len(container.get_text(strip=True))
            if text_len > best_len:
                best = container
                best_len = text_len
    if best and best_len > 200:
        return _soup_to_markdown(best)
    return None


def _extract_full_page(html):
    """Convert entire page to markdown, stripping nav/header/footer."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    for sel in ["[role='navigation']", "[role='banner']", "[role='contentinfo']",
                ".sidebar", "#sidebar", ".nav", ".menu", ".breadcrumb",
                ".cookie-banner", ".cookie-consent", "#onetrust-banner-sdk"]:
        for el in soup.select(sel):
            el.decompose()
    return _soup_to_markdown(soup)


def _count_headings(html):
    """Count h2/h3 headings as a proxy for structural density."""
    soup = BeautifulSoup(html, "lxml")
    return len(soup.find_all(["h2", "h3"]))


def _apply_sanitizer(text):
    """Run the prompt-injection sanitizer; prepend a warning header if it fired.

    Shared by clean_html (HTML pipeline) and fetch_pdf (PDF pipeline).
    """
    if not _sanitize or not text:
        return text
    cleaned, findings = _sanitize(text)
    if findings:
        return ("[Emberwalk: potential prompt injection content was found and "
                "removed from this page]\n\n" + cleaned)
    return cleaned


def clean_html(html, url):
    if not html:
        return None, None

    error = _detect_error_page(html)
    if error:
        print(f"  Detected error page ({error}), skipping", file=sys.stderr)
        return None, None

    visible_len = _visible_text_len(html)
    heading_count = _count_headings(html)

    # Compute all tiers, then choose the best
    traf_text = trafilatura.extract(html, output_format="txt", include_links=True, include_tables=True)
    traf_len = len(traf_text.strip()) if traf_text else 0
    traf_ratio = traf_len / max(visible_len, 1)

    container_text = _extract_main_container(html)
    container_len = len(container_text.strip()) if container_text else 0

    full_text = _extract_full_page(html)
    full_len = len(full_text.strip()) if full_text else 0

    chosen = None
    tier_used = None
    warning = None

    # For structurally dense pages (many headings), prefer container over trafilatura
    # since trafilatura is an article extractor that often truncates reference docs
    traf_ok = traf_text and traf_len >= 100 and traf_ratio > 0.15
    container_ok = container_text and container_len >= 200

    if traf_ok and container_ok:
        # Both viable — pick the more complete one
        # If container is substantially larger, it likely captured content trafilatura missed
        if container_len > traf_len * 1.5 or (heading_count >= 10 and container_len > traf_len):
            chosen, tier_used = container_text, "container"
            print(f"  Tier 2 (container) chosen over Tier 1: {container_len} vs {traf_len} chars, {heading_count} headings", file=sys.stderr)
        else:
            chosen, tier_used = traf_text, "trafilatura"
    elif traf_ok:
        chosen, tier_used = traf_text, "trafilatura"
    elif container_ok:
        chosen, tier_used = container_text, "container"
        if traf_text:
            print(f"  Trafilatura captured only {traf_ratio:.0%} of page, using container extraction", file=sys.stderr)
    elif full_text and full_len >= 100:
        chosen, tier_used = full_text, "full_page"
    else:
        return None, None

    # Completeness check: warn if chosen output is suspiciously thin
    chosen_len = len(chosen.strip())
    chosen_ratio = chosen_len / max(visible_len, 1)
    if chosen_ratio < 0.10 and visible_len > 1000:
        warning = f"possible truncation ({chosen_ratio:.0%} of visible text, tier={tier_used})"
        print(f"  WARNING: {warning}", file=sys.stderr)
    elif heading_count >= 5:
        # Count headings surviving in output as a structural check
        output_headings = len(re.findall(r'^#{1,3}\s', chosen, re.MULTILINE))
        if output_headings < heading_count * 0.3:
            warning = f"possible truncation ({output_headings}/{heading_count} headings preserved, tier={tier_used})"
            print(f"  WARNING: {warning}", file=sys.stderr)

    return _apply_sanitizer(chosen), warning


# -- Output ------------------------------------------------------------------

def _storage_note():
    """Return a one-line storage summary for research_output/."""
    root = Path("research_output")
    if not root.exists():
        return None
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    if total >= 1_000_000_000:
        size_str = f"{total / 1_000_000_000:.1f} GB"
    elif total >= 1_000_000:
        size_str = f"{total / 1_000_000:.1f} MB"
    else:
        size_str = f"{total / 1_000:.1f} KB"
    try:
        display = "~/" + str(root.resolve().relative_to(Path.home()))
    except ValueError:
        display = str(root.resolve())
    return f"> Storage: {display} is {size_str}"


def _pdf_title(url):
    """Derive a human title for a PDF from its URL filename."""
    name = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return name or url


def slugify(text, max_len=60):
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:max_len].rstrip('-')


def write_output(query, results, contents, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok_count = len([c for c, _ in contents if c])
    warn_count = len([w for _, w in contents if w])
    index_lines = [
        f"# Research: {query}",
        f"Date: {now}",
        f"Results: {ok_count}/{len(results)} pages fetched successfully"
        + (f" ({warn_count} with warnings)" if warn_count else ""),
        "",
        "---",
        "",
    ]

    file_count = 0
    for i, (result, (content, warning)) in enumerate(zip(results, contents)):
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

        warn_flag = f" -- WARNING: {warning}" if warning else ""
        index_lines.append(f"- **[{result['title']}]({filename})**{warn_flag}")
        index_lines.append(f"  {result['snippet']}")
        index_lines.append("")

    index_path = output_dir / "index.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")
    print(f"\nOutput written to {output_dir}/")
    print(f"  {file_count} page files + index.md")
    note = _storage_note()
    if note:
        print(note)
    return str(index_path)


# -- CLI ---------------------------------------------------------------------

def run_cli(args):
    try:
        results = search(args.query, max_results=args.results, use_brave=args.brave)
    except Exception as e:
        print(_diagnose_failure("search", repr(e)), file=sys.stderr)
        sys.exit(1)
    if not results:
        print("No search results found.", file=sys.stderr)
        sys.exit(1)

    session = make_session()
    contents = []
    for i, r in enumerate(results):
        print(f"[{i+1}/{len(results)}] Fetching: {r['title'][:60]}")
        content, warning = fetch_and_extract(r["url"], session, delay=args.delay)
        contents.append((content, warning))
        if content:
            warn_msg = f" [WARNING: {warning}]" if warning else ""
            print(f"  OK ({len(content)} chars){warn_msg}")
        else:
            print("  FAILED — no usable content")

    if _all_fetches_failed(contents):
        print(_diagnose_failure(f"all {len(contents)} fetches",
                                "every page returned empty"), file=sys.stderr)

    output_dir = args.output or str(Path("research_output") / slugify(args.query))
    write_output(args.query, results, contents, output_dir)


# -- MCP Server --------------------------------------------------------------

def _write_fetch_output(urls_and_titles, contents, output_dir):
    """Write fetched pages to disk and return index content."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok_count = len([c for c, _ in contents if c])
    warn_count = len([w for _, w in contents if w])
    index_lines = [
        f"# Fetched Pages",
        f"Date: {now}",
        f"Results: {ok_count}/{len(urls_and_titles)} pages fetched successfully"
        + (f" ({warn_count} with warnings)" if warn_count else ""),
        "",
        "---",
        "",
    ]

    file_count = 0
    for (url, title), (content, warning) in zip(urls_and_titles, contents):
        if not content:
            index_lines.append(f"- **{title or url}** — FAILED TO FETCH")
            index_lines.append(f"  {url}")
            index_lines.append("")
            continue

        file_count += 1
        title_slug = slugify(title or url, max_len=40)
        filename = f"{file_count:02d}_{title_slug}.md"

        page_md = "\n".join([
            f"# {title or url}",
            f"URL: {url}",
            f"Fetched: {now}",
            "",
            "---",
            "",
            content,
        ])
        (output_dir / filename).write_text(page_md, encoding="utf-8")

        warn_flag = f" -- WARNING: {warning}" if warning else ""
        index_lines.append(f"- **[{title or url}]({filename})**{warn_flag}")
        index_lines.append(f"  {url}")
        index_lines.append("")

    note = _storage_note()
    if note:
        index_lines += ["", note]

    index_path = output_dir / "index.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")
    return str(index_path)


def run_mcp_server():
    import json as _json
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "emberwalk",
        instructions=(
            "Emberwalk — lightweight web research.\n\n"
            "Recommended workflow:\n"
            "1. Call ew_search to get a list of candidate URLs + snippets (fast, no fetching)\n"
            "2. Review the snippets and pick the most relevant URLs\n"
            "3. Call ew_fetch with those URLs to get clean markdown files\n\n"
            "This two-step approach lets you skip irrelevant pages instead of fetching everything blind."
        ),
    )

    @mcp.tool()
    def ew_search(query: str, max_results: int = 20, use_brave: bool = False) -> str:
        """Search the web and return a list of candidate URLs with titles and snippets.

        This is fast (no page fetching). Review the results and pass the best
        URLs to ew_fetch to collect the actual page content.

        Args:
            query: The search query
            max_results: Number of results to return (default 20, max 50)
            use_brave: Use Brave Search instead of DuckDuckGo (requires BRAVE_API_KEY env var)

        Returns:
            JSON list of {url, title, snippet} objects
        """
        try:
            results = search(query, max_results=min(max_results, 50), use_brave=use_brave)
        except Exception as e:
            return _diagnose_failure("search", repr(e))
        if not results:
            return "No search results found."
        return _json.dumps(results, indent=2)

    @mcp.tool()
    def ew_fetch(urls: list[str], output_dir: str = "", subject: str = "") -> str:
        """Fetch specific URLs, extract clean markdown, and save to disk.

        Call this after ew_search to fetch only the pages you actually want.
        Also works with URLs from any source (not just search results).

        Args:
            urls: List of URLs to fetch
            output_dir: Where to save output (default: research_output/fetch-{subject}-{timestamp}/)
            subject: Optional 1-2 word topic (e.g. "monitors") used to name the
                default output folder so it's recognizable later. Omit for ad-hoc fetches.

        Returns:
            The content of the generated index.md file listing all fetched pages
        """
        if not urls:
            return "No URLs provided."

        session = make_session()
        urls_and_titles = []
        contents = []
        for i, url in enumerate(urls):
            print(f"[{i+1}/{len(urls)}] Fetching: {url[:80]}")
            if _looks_like_pdf(url):
                content, warning = fetch_pdf(url, session, delay=1.5)
                title = _pdf_title(url)
            else:
                html = fetch_page(url, session, delay=1.5)
                content, warning = clean_html(html, url)
                title = ""
                if html:
                    soup = BeautifulSoup(html, "lxml")
                    title_tag = soup.find("title")
                    if title_tag:
                        title = title_tag.get_text(strip=True)
            urls_and_titles.append((url, title))
            contents.append((content, warning))

        if not output_dir:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            subject_slug = slugify(subject, max_len=30) if subject else ""
            folder = f"fetch-{subject_slug}-{ts}" if subject_slug else f"fetch-{ts}"
            output_dir = str(Path("research_output") / folder)

        index_path = _write_fetch_output(urls_and_titles, contents, output_dir)
        index_text = Path(index_path).read_text(encoding="utf-8")
        if _all_fetches_failed(contents):
            diag = _diagnose_failure(f"all {len(contents)} fetches",
                                     "every page returned empty")
            index_text = f"{diag}\n\n---\n\n{index_text}"
        return index_text

    @mcp.tool()
    def ew_research(query: str, max_results: int = 10, use_brave: bool = False, output_dir: str = "") -> str:
        """Search and fetch in one step. Convenience tool when you want all top results.

        For more control, use ew_search + ew_fetch instead.

        Args:
            query: The search query
            max_results: Number of pages to fetch (default 10)
            use_brave: Use Brave Search instead of DuckDuckGo (requires BRAVE_API_KEY env var)
            output_dir: Where to save output (default: research_output/{query-slug}/)

        Returns:
            The content of the generated index.md file
        """
        try:
            results = search(query, max_results=max_results, use_brave=use_brave)
        except Exception as e:
            return _diagnose_failure("search", repr(e))
        if not results:
            return "No search results found."

        session = make_session()
        contents = []
        for i, r in enumerate(results):
            content, warning = fetch_and_extract(r["url"], session, delay=1.5)
            contents.append((content, warning))

        if not output_dir:
            output_dir = str(Path("research_output") / slugify(query))

        index_path = write_output(query, results, contents, output_dir)
        index_text = Path(index_path).read_text(encoding="utf-8")
        if _all_fetches_failed(contents):
            diag = _diagnose_failure(f"all {len(contents)} fetches",
                                     "every page returned empty")
            index_text = f"{diag}\n\n---\n\n{index_text}"
        return index_text

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
