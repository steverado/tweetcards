#!/usr/bin/env python3
"""
x_csv_to_posts.py — normalize X's native analytics CSV export into posts.json.

X's per-post analytics export (analytics.twitter.com -> Export data) is the free
path. X renames those columns every year or so, so this maps a range of known
aliases and has an --inspect mode to show you what it detected before committing.

The export is per-account and doesn't name the handle, so pass --account.
Retweets and replies are dropped automatically (they render badly as standalone
cards). Run once per account CSV; use --append to accumulate into one posts.json.

Usage:
    python x_csv_to_posts.py export.csv --account techbromemes
    python x_csv_to_posts.py export.csv --account techbromemes --append
    python x_csv_to_posts.py export.csv --inspect      # show detected columns
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# lowercased-header -> canonical field. First match wins.
ALIASES = {
    "id": ["tweet id", "post id", "id"],
    "text": ["tweet text", "post text", "text", "tweet", "post"],
    "created_at": ["time", "date", "created at", "post date", "timestamp"],
    "impressions": ["impressions", "views", "impression"],
    "likes": ["likes", "favorites", "favs", "like"],
    "reposts": ["retweets", "reposts", "retweet", "repost"],
    "replies": ["replies", "reply"],
    "url": ["permalink", "tweet permalink", "post link", "tweet url", "url", "link"],
}


def build_colmap(headers):
    lower = {h.lower().strip(): h for h in headers}
    colmap = {}
    for field, opts in ALIASES.items():
        for o in opts:
            if o in lower:
                colmap[field] = lower[o]
                break
    return colmap


def to_int(v):
    if v is None:
        return 0
    s = re.sub(r"[^0-9]", "", str(v))
    return int(s) if s else 0


def is_retweet_or_reply(text):
    t = (text or "").lstrip()
    return t.startswith("RT @") or t.startswith("@")


def clean_text(text):
    """Strip trailing t.co media links (image/video posts carry a bare t.co URL
    that X renders as the attached media, not as body text). Returns cleaned text;
    empty means an image-only post with no caption -> not a text card."""
    t = re.sub(r"https?://t\.co/\w+", "", text or "")
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def norm_url(row_url, handle, pid):
    if row_url and str(row_url).startswith("http"):
        return str(row_url).strip()
    if handle and pid:
        return "https://x.com/%s/status/%s" % (handle, pid)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="X analytics CSV export")
    ap.add_argument("--account", help="handle to tag these rows with (e.g. techbromemes)")
    ap.add_argument("--out", "-o", default="posts.json")
    ap.add_argument("--append", action="store_true", help="merge into existing out file")
    ap.add_argument("--inspect", action="store_true", help="print column mapping and exit")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    if not csv_path.exists():
        sys.exit("CSV not found: %s" % csv_path)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        colmap = build_colmap(headers)

        if args.inspect:
            print("Columns in file:")
            for h in headers:
                print("  - %s" % h)
            print("\nDetected mapping:")
            for field in ALIASES:
                print("  %-12s -> %s" % (field, colmap.get(field, "(missing)")))
            missing = [f for f in ("text", "impressions") if f not in colmap]
            if missing:
                print("\nWARNING: required fields missing: %s" % ", ".join(missing))
            return

        if "text" not in colmap or "impressions" not in colmap:
            sys.exit("Could not find text/impressions columns. Run --inspect to see headers.")
        if not args.account:
            sys.exit("--account is required (the export doesn't name the handle).")

        rows = list(reader)

    out = []
    dropped = 0
    for i, r in enumerate(rows):
        raw = (r.get(colmap["text"]) or "").strip()
        if is_retweet_or_reply(raw):
            dropped += 1
            continue
        # Keep image/video posts (empty caption after stripping the t.co media
        # link) — the meme IS the image; render_cards.py fetches the media.
        text = clean_text(raw)
        pid = (r.get(colmap.get("id", "")) or "").strip() or ("%s_%d" % (args.account, i))
        out.append({
            "id": pid,
            "account": args.account,
            "text": text,
            "impressions": to_int(r.get(colmap["impressions"])),
            "likes": to_int(r.get(colmap.get("likes", ""))),
            "reposts": to_int(r.get(colmap.get("reposts", ""))),
            "replies": to_int(r.get(colmap.get("replies", ""))),
            "created_at": (r.get(colmap.get("created_at", "")) or "").strip(),
            "url": norm_url(r.get(colmap.get("url", "")), args.account, pid),
        })

    out_path = ROOT / args.out
    if args.append and out_path.exists():
        existing = json.loads(out_path.read_text())
        seen = {(p.get("account"), p.get("id")) for p in existing}
        merged = existing + [p for p in out if (p["account"], p["id"]) not in seen]
        out = merged

    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print("Wrote %d post(s) for @%s -> %s (dropped %d retweet/reply)"
          % (len([p for p in out if p.get("account") == args.account]),
             args.account, args.out, dropped))


if __name__ == "__main__":
    main()
