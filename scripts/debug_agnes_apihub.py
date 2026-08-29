"""Debug script for the Agnes AI apihub endpoint.

Run this to figure out which URL the apihub actually expects.
The user pasted `https://apihub.agnes-ai.com/v1/chat/completions`
but the apihub returned 404 with the path doubled to
`/v1/chat/v1/chat/completions` — suggesting the apihub does path
concatenation on the user-supplied path.

This script tries 4 candidate URLs and reports what each one returns.
Usage: python scripts/debug_agnes_apihub.py
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.request

API_KEY = "REDACTED"  # from screenshot
HOST = "https://apihub.agnes-ai.com"
MODEL = "agnes-2.5-flash"

# Candidate URLs the apihub might accept. The user's paste
# (`/v1/chat/completions`) returns 404 with a doubled path;
# maybe a different URL works.
CANDIDATES = [
    HOST + "/v1/chat/completions",  # standard OpenAI (user tried — 404 with doubled path)
    HOST + "/v1/chat",              # apihub base — if it auto-appends /v1/chat/completions
    HOST + "/v1",                   # just the versioned base
    HOST + "/v1/completions",       # another common variant
    HOST,                           # bare host
]

BODY = json.dumps({
    "model": MODEL,
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "ping"}],
}).encode("utf-8")


def probe(url: str) -> dict:
    """POST the minimal chat-completions body to ``url`` and report."""
    req = urllib.request.Request(url, data=BODY, method="POST")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {
                "url": url,
                "ok": True,
                "status": resp.status,
                "body": resp.read().decode("utf-8", errors="replace")[:200],
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return {
            "url": url,
            "ok": False,
            "status": exc.code,
            "body": body[:300],
        }
    except (urllib.error.URLError, OSError) as exc:
        return {
            "url": url,
            "ok": False,
            "status": 0,
            "body": f"connection failed: {exc}",
        }


def main() -> int:
    print(f"Probing {HOST} with model={MODEL!r}")
    print("=" * 70)
    for url in CANDIDATES:
        result = probe(url)
        marker = "OK " if result["ok"] else "FAIL"
        print(f"\n[{marker}] POST {result['url']}")
        print(f"      status: {result['status']}")
        body = result["body"].strip()
        if body:
            # Show first line + total length
            first = body.split("\n", 1)[0]
            print(f"      body:   {first[:200]}")
            if len(body) > len(first):
                print(f"               (truncated, total {len(body)} chars)")

    print("\n" + "=" * 70)
    print("Interpretation:")
    print("  - 200 / 2xx = apihub accepted the request (paste this URL)")
    print("  - 4xx with 'Invalid URL' = apihub routing bug (path doubling)")
    print("  - 401 = apihub rejected the API key (key is wrong)")
    print("  - 400 with 'model' = apihub accepted URL but model is wrong")
    print("  - 0/connection = DNS/network failure (URL host is wrong)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
