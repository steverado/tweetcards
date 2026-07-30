#!/usr/bin/env python3
"""
prerender.py — LOCAL step. Render the next N carousels per account and append them
to queue.json as `pending`, ready for the daily cloud job to queue one at a time.

Why local, not in CI: rendering needs Playwright/Chromium and hits X's public
syndication endpoint, which is flaky from datacenter IPs. We do the heavy, fragile
part here (where X + a real browser work), commit the finished PNGs to the public
repo for jsDelivr, and leave the daily GitHub Action a tiny stdlib-only HTTP script.

What it does, per account:
  1. Rank the backlog (posts.json) by impressions, excluding any X post already used
     (present in queue.json `pending` or `posted`) — so no meme is ever repeated.
  2. Take the next `--batches N` carousels of `--per-carousel` slides each.
  3. Render each slide (reusing render_cards) into out/<account>/<key>_sNN.png.
  4. git add/commit/push the new slides, then pin jsDelivr media_urls to that commit
     SHA and append the carousels to queue.json `pending`.

Run it whenever the pending queue gets low (the daily job warns you). Idempotent:
re-running just adds more fresh batches; nothing already used comes back.

Usage:
    python prerender.py                     # 7 carousels/account, 8 slides each
    python prerender.py --batches 14        # ~2 weeks of runway per account
    python prerender.py --dry-run           # render + show, no commit/push/queue.json
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import render_cards as rc  # reuse fetch_tweet, build_html, FIT_JS, CSS_*, SCALE

ROOT = Path(__file__).resolve().parent


def run(cmd, check=True):
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit("`%s` failed:\n%s" % (" ".join(cmd), r.stderr.strip()))
    return r.stdout.strip()


def parse_remote(url):
    import re
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url.strip())
    if not m:
        sys.exit("origin isn't a recognizable GitHub URL: %s" % url)
    return m.group(1), m.group(2)


def load_queue():
    p = ROOT / "queue.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"pending": [], "posted": []}


def used_x_ids(queue):
    ids = set()
    for bucket in ("pending", "posted"):
        for c in queue.get(bucket, []):
            ids.update(str(i) for i in c.get("x_post_ids", []))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=7,
                    help="carousels to render PER ACCOUNT this run")
    ap.add_argument("--per-carousel", type=int, default=8, help="slides per carousel (2-10)")
    ap.add_argument("--posts", default="posts.json")
    ap.add_argument("--accounts", default="accounts.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    posts_path = ROOT / args.posts
    if not posts_path.exists():
        sys.exit("No %s — run x_csv_to_posts.py first." % args.posts)
    posts = json.loads(posts_path.read_text())
    accounts = json.loads((ROOT / args.accounts).read_text())
    queue = load_queue()
    used = used_x_ids(queue)

    # Group backlog per account, ranked by impressions, excluding already-used memes.
    by_acct = {}
    for p in posts:
        h = p.get("account")
        if h not in accounts:
            continue
        if str(p.get("id")) in used:
            continue
        if int(p.get("impressions", 0)) < int(accounts[h].get("min_impressions", 0)):
            continue
        by_acct.setdefault(h, []).append(p)
    for h in by_acct:
        by_acct[h].sort(key=lambda p: int(p.get("impressions", 0)), reverse=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright missing. Run: pip install -r requirements.txt && playwright install chromium")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    out_dir = ROOT / "out"
    new_carousels = []  # (account, key, caption, x_post_ids, slide_files)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": rc.CSS_W, "height": rc.CSS_H},
                                device_scale_factor=rc.SCALE)
        for h, plist in by_acct.items():
            acct = accounts[h]
            acct_out = out_dir / h
            acct_out.mkdir(parents=True, exist_ok=True)
            made = 0
            idx = 0
            print("@%s: %d unused post(s) available; rendering up to %d carousel(s)..."
                  % (h, len(plist), args.batches))
            while made < args.batches and idx < len(plist):
                # Gather the next per-carousel renderable posts (skip ones with no media+no text).
                group = []
                while len(group) < args.per_carousel and idx < len(plist):
                    post = plist[idx]; idx += 1
                    tw = rc.fetch_tweet(post["id"])
                    if not tw.get("media_path") and not (post.get("text") or "").strip():
                        continue
                    group.append((post, tw))
                if len(group) < 2:  # HumanPost needs >=2 images
                    break
                cnum = made + 1
                key = "%s_%s_c%02d" % (h, stamp, cnum)
                slide_files, x_ids = [], []
                for si, (post, tw) in enumerate(group, 1):
                    page.set_content(rc.build_html(post, acct, tw), wait_until="load")
                    page.wait_for_function(
                        "() => { const i=document.getElementById('media');"
                        " return !i || (i.complete && i.naturalHeight>0); }")
                    page.evaluate(rc.FIT_JS)
                    page.wait_for_timeout(30)
                    fname = "%s_s%02d.png" % (key, si)
                    fpath = acct_out / fname
                    page.screenshot(path=str(fpath))
                    slide_files.append(str(fpath.relative_to(ROOT)))
                    x_ids.append(str(post.get("id")))
                new_carousels.append((h, key, acct.get("caption", ""), x_ids, slide_files))
                made += 1
                print("  rendered %s (%d slides)" % (key, len(slide_files)))
        browser.close()

    if not new_carousels:
        print("Nothing new to render — backlog may be exhausted for all accounts.")
        return

    if args.dry_run:
        print("\nDRY RUN — %d carousel(s), not committed:" % len(new_carousels))
        for h, key, cap, xids, files in new_carousels:
            print("  %s (%s): %d slides" % (key, cap, len(files)))
        return

    # Commit the new slides so jsDelivr can serve them, then pin URLs to the SHA.
    remote = run(["git", "remote", "get-url", "origin"])
    owner, repo = parse_remote(remote)
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    run(["git", "add", "out"])
    run(["git", "commit", "-m", "prerender: %d carousel(s) @%s" % (len(new_carousels), stamp)],
        check=False)
    run(["git", "push", "origin", branch])
    sha = run(["git", "rev-parse", "HEAD"])
    base = "https://cdn.jsdelivr.net/gh/%s/%s@%s/" % (owner, repo, sha)

    for h, key, cap, xids, files in new_carousels:
        queue["pending"].append({
            "account": h,
            "key": key,
            "caption": cap,
            "x_post_ids": xids,
            "media_urls": [base + f for f in files],
        })
    (ROOT / "queue.json").write_text(json.dumps(queue, indent=2))
    run(["git", "add", "queue.json"])
    run(["git", "commit", "-m", "prerender: queue %d pending carousel(s)" % len(new_carousels)],
        check=False)
    run(["git", "push", "origin", branch])

    from collections import Counter
    per = Counter(c["account"] for c in queue["pending"])
    print("\nAppended %d carousel(s), pinned @%s. Pending queue now:" % (len(new_carousels), sha[:10]))
    for h in accounts:
        print("  %s: %d pending (~%d days)" % (h, per.get(h, 0), per.get(h, 0)))


if __name__ == "__main__":
    main()
