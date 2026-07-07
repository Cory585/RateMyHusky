"""
Ping IndexNow (Bing/Copilot/ChatGPT search) with the site's current sitemap URLs.

WHY: Bing's crawler can take weeks to notice sitemap changes on its own. IndexNow
lets us push the URL list directly so Bing re-crawls within hours instead.

WHEN TO RUN:
  - Once, right after verifying the site in Bing Webmaster Tools.
  - Again after any deploy or data refresh that changes page content (new
    professors/courses, prerendered content, etc).

USAGE:
  python indexnow_ping.py --dry-run
  python indexnow_ping.py --sitemap ../frontend/public/sitemap.xml --dry-run
  python indexnow_ping.py
  python indexnow_ping.py --sitemap https://ratemyhusky.com/sitemap.xml

The key is read from the committed frontend/public/<32-hex>.txt file unless
--key is passed.
"""
import os
import re
import sys
import glob
import html
import argparse
import requests

HOST = "ratemyhusky.com"
DEFAULT_SITEMAP = "https://ratemyhusky.com/sitemap.xml"
INDEXNOW_URL = "https://api.indexnow.org/indexnow"
BATCH_SIZE = 10000
KEY_RE = re.compile(r"^[0-9a-f]{32}$")


def find_key():
    pattern = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "*.txt")
    for path in glob.glob(pattern):
        name = os.path.splitext(os.path.basename(path))[0]
        if KEY_RE.match(name):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content == name:
                return name
    return None


def load_sitemap(path_or_url):
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        try:
            resp = requests.get(path_or_url, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            sys.exit(f"Failed to fetch sitemap {path_or_url}: {e}")
        return resp.text
    with open(path_or_url, "r", encoding="utf-8") as f:
        return f.read()


def parse_urls(xml_text):
    # the sitemap generator XML-escapes & < > ' " in slugs; decode them back
    return [html.unescape(loc) for loc in re.findall(r"<loc>(.*?)</loc>", xml_text)]


def batches(urls, size=BATCH_SIZE):
    for i in range(0, len(urls), size):
        yield urls[i:i + size]


def ping(key, urls):
    key_location = f"https://{HOST}/{key}.txt"
    for n, batch in enumerate(batches(urls), 1):
        payload = {
            "host": HOST,
            "key": key,
            "keyLocation": key_location,
            "urlList": batch,
        }
        try:
            resp = requests.post(INDEXNOW_URL, json=payload, timeout=30)
            print(f"batch {n} ({len(batch)} URLs) -> {resp.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"batch {n} ({len(batch)} URLs) -> FAILED: {e}")


def main():
    p = argparse.ArgumentParser(description="Ping IndexNow with sitemap URLs.")
    p.add_argument("--sitemap", default=DEFAULT_SITEMAP,
                   help=f"sitemap path or URL (default {DEFAULT_SITEMAP})")
    p.add_argument("--key", help="IndexNow key (default: read from frontend/public/<key>.txt)")
    p.add_argument("--dry-run", action="store_true", help="parse + batch + print counts only, no network POST")
    args = p.parse_args()

    key = args.key or find_key()
    if not key:
        sys.exit("No IndexNow key found. Pass --key or commit frontend/public/<32-hex>.txt")

    urls = parse_urls(load_sitemap(args.sitemap))
    batch_list = list(batches(urls))

    if args.dry_run:
        print(f"URLs found: {len(urls)}")
        print(f"Batches ({BATCH_SIZE} max each): {len(batch_list)}")
        print("First few URLs:")
        for u in urls[:5]:
            print(f"  {u}")
        return

    ping(key, urls)


if __name__ == "__main__":
    main()
