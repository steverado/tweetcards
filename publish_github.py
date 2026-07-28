#!/usr/bin/env python3
"""
publish_github.py — host the rendered slides on GitHub + jsDelivr and write the
CDN URLs back into manifest.json, ready for HumanPost's upload_media / create_post.

Why this instead of R2: no new account, no credentials beyond a git remote, free.
The CDN URL is pinned to the commit SHA, so it's immutable and never serves a stale
cache the way an @main URL can. Move to R2 later if the repo gets heavy; only the
`media_urls` values change.

    jsDelivr form: https://cdn.jsdelivr.net/gh/<owner>/<repo>@<sha>/<path>

Prereq: this repo has a GitHub remote (git remote add origin git@github.com:you/repo.git).

Usage:
    python publish_github.py                 # commit out/, push, fill media_urls
    python publish_github.py --dry-run       # show URLs without committing/pushing
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd, check=True):
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit("`%s` failed:\n%s" % (" ".join(cmd), r.stderr.strip()))
    return r.stdout.strip()


def parse_remote(url):
    """Return (owner, repo) from an ssh or https GitHub remote."""
    url = url.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        sys.exit("Remote isn't a recognizable GitHub URL: %s" % url)
    return m.group(1), m.group(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="manifest.json")
    ap.add_argument("--branch", default=None, help="defaults to current branch")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    man_path = ROOT / args.manifest
    if not man_path.exists():
        sys.exit("No %s — run render_cards.py first." % args.manifest)
    manifest = json.loads(man_path.read_text())

    remote = run(["git", "remote", "get-url", "origin"], check=False)
    if not remote:
        sys.exit("No 'origin' remote. Add one: git remote add origin <github url>")
    owner, repo = parse_remote(remote)
    branch = args.branch or run(["git", "rev-parse", "--abbrev-ref", "HEAD"])

    if not args.dry_run:
        run(["git", "add", "out", args.manifest])
        # commit may be a no-op if nothing changed; don't hard-fail on that.
        run(["git", "commit", "-m", "publish: rendered carousel slides"], check=False)
        run(["git", "push", "origin", branch])

    sha = run(["git", "rev-parse", "HEAD"])
    base = "https://cdn.jsdelivr.net/gh/%s/%s@%s/" % (owner, repo, sha)

    for carousel in manifest:
        urls = []
        for slide in sorted(carousel["slides"], key=lambda s: s["order"]):
            urls.append(base + slide["file"])
        carousel["media_urls"] = urls

    if args.dry_run:
        print("DRY RUN — commit pinned to %s" % sha[:10])
        for c in manifest:
            print("\n%s carousel %d:" % (c["account"], c["carousel"]))
            for u in c["media_urls"]:
                print("  " + u)
        return

    man_path.write_text(json.dumps(manifest, indent=2))
    total = sum(len(c["media_urls"]) for c in manifest)
    print("Pushed %s and wrote %d media_url(s) into %s (pinned @%s)"
          % (branch, total, args.manifest, sha[:10]))
    print("Slide order = carousel order — HumanPost posts them in this sequence.")


if __name__ == "__main__":
    main()
