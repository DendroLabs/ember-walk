#!/usr/bin/env python3
"""Run sanitizer against local injection test pages.

Starts injection_server.py in a background thread, fetches each test URL
through emberwalk's full clean_html() pipeline, and reports what was caught.

Usage: python3 run_injection_tests.py [--strict]
"""

import sys
import os
import time
import threading
import urllib.request
from pathlib import Path

# Add emberwalk root to path so we can import emberwalk and sanitizer
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Start the server in a thread before importing emberwalk
import importlib.util
spec = importlib.util.spec_from_file_location(
    "injection_server",
    Path(__file__).parent / "injection_server.py",
)
server_mod = importlib.util.load_from_spec = spec
server_src = Path(__file__).parent / "injection_server.py"

from http.server import HTTPServer
import importlib.util as _ilu

def _start_server(port=9191):
    spec = _ilu.spec_from_file_location("injection_server", server_src)
    mod = _ilu.module_from_spec(spec)
    # Temporarily reset argv so the server module doesn't parse --strict
    saved, sys.argv = sys.argv, [sys.argv[0], str(port)]
    spec.loader.exec_module(mod)
    sys.argv = saved
    httpd = HTTPServer(("127.0.0.1", port), mod.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port

import emberwalk
from sanitizer import sanitize as _sanitize_direct

STRICT = "--strict" in sys.argv

# Expected results: (path, description, should_catch)
TESTS = [
    ("/instruction-override", "Instruction override (CRITICAL)",      True),
    ("/role-hijack",          "Role hijacking (CRITICAL)",            True),
    ("/delimiter",            "Delimiter tokens (CRITICAL)",          True),
    ("/system-extract",       "System prompt extraction (CRITICAL)",  True),
    ("/indirect",             "Indirect/labelled injection (HIGH)",   True),
    ("/exfil",                "Data exfiltration (HIGH)",             True),
    ("/unicode",              "Invisible Unicode (MEDIUM)",           True),
    ("/homoglyph",            "Cyrillic homoglyphs (MEDIUM)",         True),
    ("/base64",               "Base64-encoded payload (HIGH)",        True),
    # HTML comments are stripped by the HTML parser before sanitization, so
    # this is neutralized by the pipeline itself, not the sanitizer pattern.
    # The LOW pattern applies when raw HTML is sanitized directly.
    ("/html-comment",         "Hidden HTML comment (stripped by HTML parser)", False),
    ("/clean",                "Clean page (no injection)",            False),
]

COL_DESC  = 45
COL_CATCH = 8
COL_FOUND = 8


def _fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "emberwalk-test/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _run(port):
    base = f"http://127.0.0.1:{port}"
    header = f"{'Description':<{COL_DESC}} {'Expect':<{COL_CATCH}} {'Found':<{COL_FOUND}} Result"
    print(header)
    print("-" * (COL_DESC + COL_CATCH + COL_FOUND + 12))

    passed = 0
    failed = 0

    for path, desc, should_catch in TESTS:
        url = base + path
        try:
            html = _fetch_html(url)
            content, _warn = emberwalk.clean_html(html, url)
            caught = "[Emberwalk: potential prompt injection" in content
            # In strict mode, re-run sanitizer directly with strict=True so LOW
            # patterns (e.g. long HTML comments) are also checked.
            if STRICT and not caught and content:
                _, extra = _sanitize_direct(content, strict=True)
                if extra:
                    caught = True
        except Exception as e:
            print(f"{'ERROR: ' + str(e):<{COL_DESC}} {'?':<{COL_CATCH}} {'?':<{COL_FOUND}} ERROR")
            failed += 1
            continue

        expect_str = "CATCH" if should_catch else "PASS"
        found_str  = "caught" if caught else "clean"

        ok = (caught == should_catch)
        result = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"{desc:<{COL_DESC}} {expect_str:<{COL_CATCH}} {found_str:<{COL_FOUND}} {result}")

    print("-" * (COL_DESC + COL_CATCH + COL_FOUND + 12))
    total = passed + failed
    print(f"{passed}/{total} passed" + (" (--strict mode)" if STRICT else ""))
    return failed


def main():
    print("Starting injection test server...", end=" ", flush=True)
    httpd, port = _start_server()
    # Brief pause for server to bind
    time.sleep(0.15)
    print(f"up on port {port}\n")

    try:
        failures = _run(port)
    finally:
        httpd.shutdown()

    sys.exit(failures)


if __name__ == "__main__":
    main()
