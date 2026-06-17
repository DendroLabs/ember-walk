#!/usr/bin/env python3
"""Benchmark: Emberwalk vs Firecrawl on Cisco IOS-XR docs."""

import time
import json
import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from pathlib import Path
import emberwalk as ew

FIRECRAWL_URL = "http://localhost:3002"

CHAPTERS = [
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_preface_00.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_0111.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_01.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_01000.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_010.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_011.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_0100.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/m-rip-commands-fretta.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_0101.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_chapter_0110.html",
    "https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/b-ncs5500-routing-cli-reference/b-ncs5500-routing-cli-reference_CLT_chapter.html",
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def fetch_emberwalk(url, browser):
    """Fetch with Emberwalk's full pipeline: fetch + 3-tier clean."""
    start = time.time()
    session = ew.make_session()
    html = ew.fetch_page(url, session, delay=0, browser=browser)
    text = ew.clean_html(html, url)
    elapsed = time.time() - start
    if not text:
        return {"time": elapsed, "chars": 0, "error": "no content extracted"}
    return {"time": elapsed, "chars": len(text), "text": text}


def fetch_firecrawl(url):
    """Fetch with Firecrawl's scrape API."""
    start = time.time()
    try:
        resp = requests.post(
            f"{FIRECRAWL_URL}/v1/scrape",
            json={"url": url, "formats": ["markdown"], "waitFor": 5000, "onlyMainContent": True},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        md = data.get("data", {}).get("markdown", "")
        elapsed = time.time() - start
        return {"time": elapsed, "chars": len(md), "text": md}
    except Exception as e:
        return {"time": time.time() - start, "chars": 0, "error": str(e)}


def short_name(url):
    return url.split("/")[-1].replace("b-ncs5500-routing-cli-reference_", "").replace(".html", "")


def main():
    out_dir = Path("benchmark_output")
    out_dir.mkdir(exist_ok=True)

    results = []

    # Emberwalk: reuse one browser instance across all pages
    print("=" * 70)
    print("EMBERWALK (Playwright + trafilatura)")
    print("=" * 70)
    ew_total_start = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for i, url in enumerate(CHAPTERS):
            name = short_name(url)
            print(f"  [{i+1}/{len(CHAPTERS)}] {name}...", end=" ", flush=True)
            r = fetch_emberwalk(url, browser)
            print(f"{r['time']:.1f}s, {r['chars']} chars" + (f" ERROR: {r.get('error','')}" if r.get('error') else ""))
            if r.get("text"):
                (out_dir / f"ew_{name}.md").write_text(r["text"], encoding="utf-8")
            results.append({"url": url, "name": name, "emberwalk": r})
            time.sleep(1.5)
        browser.close()
    ew_total = time.time() - ew_total_start

    # Firecrawl
    print()
    print("=" * 70)
    print("FIRECRAWL (localhost:3002)")
    print("=" * 70)
    fc_total_start = time.time()
    for i, url in enumerate(CHAPTERS):
        name = short_name(url)
        print(f"  [{i+1}/{len(CHAPTERS)}] {name}...", end=" ", flush=True)
        r = fetch_firecrawl(url)
        print(f"{r['time']:.1f}s, {r['chars']} chars" + (f" ERROR: {r.get('error','')}" if r.get('error') else ""))
        if r.get("text"):
            (out_dir / f"fc_{name}.md").write_text(r["text"], encoding="utf-8")
        results[i]["firecrawl"] = r
        time.sleep(1.5)
    fc_total = time.time() - fc_total_start

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Chapter':<25} | {'EW Time':>8} {'EW Chars':>9} | {'FC Time':>8} {'FC Chars':>9} | {'Faster':>8} {'More Content':>12}")
    print("-" * 25 + "-+-" + "-" * 19 + "-+-" + "-" * 19 + "-+-" + "-" * 22)
    for r in results:
        name = r["name"][:24]
        ew = r["emberwalk"]
        fc = r.get("firecrawl", {"time": 0, "chars": 0})
        faster = "EW" if ew["time"] < fc["time"] else "FC"
        more = "EW" if ew["chars"] > fc["chars"] else "FC"
        if ew.get("error"):
            faster = "FC"
        if fc.get("error"):
            faster = "EW"
        print(f"{name:<25} | {ew['time']:>7.1f}s {ew['chars']:>8} | {fc['time']:>7.1f}s {fc['chars']:>8} | {faster:>8} {more:>12}")

    print("-" * 25 + "-+-" + "-" * 19 + "-+-" + "-" * 19 + "-+-" + "-" * 22)
    ew_chars = sum(r["emberwalk"]["chars"] for r in results)
    fc_chars = sum(r.get("firecrawl", {}).get("chars", 0) for r in results)
    print(f"{'TOTAL':<25} | {ew_total:>7.1f}s {ew_chars:>8} | {fc_total:>7.1f}s {fc_chars:>8} |")

    # Save raw results
    for r in results:
        for key in ["emberwalk", "firecrawl"]:
            if key in r and "text" in r[key]:
                del r[key]["text"]
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed output in {out_dir}/")


if __name__ == "__main__":
    main()
