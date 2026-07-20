#!/usr/bin/env python3
"""
Fetch Kick.com follower count for a channel slug.
Writes the count to a file for FFmpeg to overlay on the stream.
"""

import json
import sys
import time
import urllib.request
import urllib.error

KICK_API = "https://kick.com/api/v2/channels/{slug}"
FALLBACK_FILE = "/tmp/kick-stream/follower_count.txt"
UPDATE_INTERVAL = 120  # seconds between API calls


def fetch_followers(slug: str) -> int | None:
    url = KICK_API.format(slug=slug)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            count = data.get("followers_count")
            if count is not None:
                return int(count)
    except Exception as e:
        print(f"[followers] API error: {e}", file=sys.stderr)
    return None


def format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def write_count(text: str, filepath: str = FALLBACK_FILE):
    with open(filepath, "w") as f:
        f.write(text)


def main():
    if len(sys.argv) < 2:
        print("Usage: get_followers.py <kick_username> [output_file]", file=sys.stderr)
        sys.exit(1)

    slug = sys.argv[1]
    outfile = sys.argv[2] if len(sys.argv) > 2 else FALLBACK_FILE
    write_count("LIVE", outfile)

    print(f"[followers] Tracking followers for: {slug}")
    while True:
        count = fetch_followers(slug)
        if count is not None:
            text = f"{format_count(count)} followers"
            print(f"[followers] {text}")
        else:
            text = "LIVE"
            print("[followers] Could not fetch count, showing LIVE")
        write_count(text, outfile)
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    main()
