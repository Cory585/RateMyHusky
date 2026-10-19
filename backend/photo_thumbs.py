"""
Make small WebP copies of every professor photo, served from the site itself.

The photos are hotlinked from ~165 university hosts at full size (up to ~1 MB,
1400 px wide) and shown in 54-160 px boxes, so each one took 2+ seconds to
appear. This downloads each professors.image_url once, shrinks its short side
to SHORT_SIDE (2x the tallest box, for retina) keeping the aspect ratio so
focus_x / focus_y still crop it the same way, and writes it to
frontend/public/photos/<thumb_name(url)>. The frontend computes the same name
(frontend/src/utils/photoThumb.ts) and falls back to the original URL when a
file is missing, so a photo added since the last run still shows.

Existing files are skipped, so a rerun only fetches new photos. Run it after
photos change, then commit frontend/public/photos/.

Reads ORACLE_DATABASE_URL from backend/.env.

Run:  python backend/photo_thumbs.py            # fetch what's missing
      python backend/photo_thumbs.py --prune    # also delete files no professor uses
"""

import argparse
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor

SHORT_SIDE = 320
QUALITY = 72
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "public", "photos")


def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def thumb_name(url):
    """cyrb53 of the URL in hex, plus .webp. Must match photoThumb.ts exactly:
    JavaScript hashes UTF-16 code units, so this does too."""
    data = url.encode("utf-16-le")
    h1, h2 = 0xDEADBEEF, 0x41C6CE57
    for i in range(0, len(data), 2):
        ch = data[i] | (data[i + 1] << 8)
        h1 = _imul(h1 ^ ch, 2654435761)
        h2 = _imul(h2 ^ ch, 1597334677)
    h1 = _imul(h1 ^ (h1 >> 16), 2246822507)
    h1 ^= _imul(h2 ^ (h2 >> 13), 3266489909)
    h2 = _imul(h2 ^ (h2 >> 16), 2246822507)
    h2 ^= _imul(h1 ^ (h1 >> 13), 3266489909)
    return format(4294967296 * (2097151 & h2) + h1, "x") + ".webp"


def make_thumbnail(raw):
    """WebP bytes of the image with its short side at most SHORT_SIDE."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    scale = SHORT_SIDE / min(w, h)
    if scale < 1:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=QUALITY, method=6)
    return out.getvalue()


def photo_urls():
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    url = os.getenv("ORACLE_DATABASE_URL")
    if not url:
        sys.exit("Missing ORACLE_DATABASE_URL in backend/.env")
    conn = psycopg2.connect(url, sslmode="require", connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT image_url FROM professors WHERE image_url IS NOT NULL AND image_url <> ''")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_one(session, url, path):
    """Returns (url, error or None)."""
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200 or not resp.content:
            return url, f"HTTP {resp.status_code}"
        thumb = make_thumbnail(resp.content)
    except Exception as e:  # one bad photo must not stop the run
        return url, f"{type(e).__name__}: {e}"
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(thumb)
    os.replace(tmp, path)
    return url, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--prune", action="store_true", help="delete thumbnails no professor uses any more")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    import requests

    os.makedirs(OUT_DIR, exist_ok=True)
    urls = photo_urls()
    wanted = {thumb_name(u): u for u in urls}
    todo = [(u, os.path.join(OUT_DIR, name)) for name, u in wanted.items()
            if not os.path.exists(os.path.join(OUT_DIR, name))]
    print(f"{len(urls)} photos, {len(urls) - len(todo)} already have thumbnails, fetching {len(todo)}")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; RateMyHusky photo thumbnails)"
    failed = []
    with ThreadPoolExecutor(args.workers) as pool:
        for i, (url, err) in enumerate(pool.map(lambda t: fetch_one(session, *t), todo), 1):
            if err:
                failed.append((url, err))
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}")

    if args.prune:
        stale = [f for f in os.listdir(OUT_DIR) if f.endswith(".webp") and f not in wanted]
        for f in stale:
            os.remove(os.path.join(OUT_DIR, f))
        print(f"pruned {len(stale)} unused thumbnails")

    total = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR) if f.endswith(".webp"))
    print(f"done: {len(todo) - len(failed)} written, {len(failed)} failed (these keep the original URL); "
          f"folder is {total / 1e6:.1f} MB")
    for url, err in failed:
        print(f"  FAILED {err}  {url}")


if __name__ == "__main__":
    main()
