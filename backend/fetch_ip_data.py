"""Boot-time download of the ipinfo ASN database and Apple's Private Relay ranges.

Runs before gunicorn (see Procfile) and always exits 0: missing data only makes
network types "unknown", which never holds a rating.
"""

import os
import sys

import requests as http_requests

IPINFO_URL = "https://ipinfo.io/data/ipinfo_lite.mmdb?token={token}"
RELAY_URL = "https://mask-api.icloud.com/egress-ip-ranges.csv"


def _fetch(url: str, path: str) -> None:
    r = http_requests.get(url, timeout=120)
    r.raise_for_status()
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, path)


def main() -> int:
    data_dir = os.environ.get("IP_DATA_DIR", "/tmp/ipdata")
    token = os.environ.get("IPINFO_TOKEN")
    os.makedirs(data_dir, exist_ok=True)
    jobs = [("ipinfo_lite.mmdb", IPINFO_URL.format(token=token) if token else None),
            ("relay.csv", RELAY_URL)]
    for name, url in jobs:
        if url is None:
            print(f"[fetch_ip_data] {name} skipped: IPINFO_TOKEN not set")
            continue
        try:
            _fetch(url, os.path.join(data_dir, name))
            print(f"[fetch_ip_data] {name} fetched")
        except Exception as e:
            reason = str(e).replace(token, "***") if token else str(e)
            print(f"[fetch_ip_data] {name} failed: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
