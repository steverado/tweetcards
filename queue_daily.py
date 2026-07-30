#!/usr/bin/env python3
"""
queue_daily.py — CLOUD step (GitHub Actions, once a day). Stdlib only: no Playwright,
no X, no posts.json. Just moves one pre-rendered carousel per account from queue.json
`pending` -> HumanPost's queue, then records it in `posted` and commits queue.json.

Per account (in accounts.json order):
  1. Take the oldest `pending` carousel for that account.
  2. Respect the daily cap (skip if the account already hit postsToday >= dailyCap).
  3. upload_media each pinned jsDelivr slide URL (in order), then create_post with the
     account's caption.
  4. On success, move the carousel pending -> posted (with the returned HumanPost id).
Then git commit/push queue.json. A human still posts it by hand from HumanPost's queue.

Warns when an account's pending queue is low so you know to run prerender.py locally.

Env:
    HUMANPOST_TOKEN   required — the ccb_live_... bearer (a GitHub Actions secret)
    MCP_URL           optional — defaults to the HumanPost cloud function

Usage:
    python queue_daily.py            # queue one carousel per eligible account
    python queue_daily.py --dry-run  # show what it would do; no calls, no commit
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MCP_URL = os.environ.get("MCP_URL", "https://us-central1-clickbaitcarousel.cloudfunctions.net/mcp")
LOW_WATERMARK = 3  # warn when an account has fewer than this many pending carousels


def mcp_call(name, arguments, token):
    """One stateless JSON-RPC tools/call against the HumanPost HTTP endpoint."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.loads(r.read())
    if "error" in payload:
        raise RuntimeError("%s failed: %s" % (name, payload["error"]))
    # HumanPost wraps the real result as JSON text inside content[0].text.
    result = payload.get("result", {})
    content = result.get("content")
    if isinstance(content, list) and content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except (ValueError, KeyError):
            return content[0]["text"]
    return result


def run(cmd, check=True):
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit("`%s` failed:\n%s" % (" ".join(cmd), r.stderr.strip()))
    return r.stdout.strip()


def caps_by_account(token):
    """accountId -> (postsToday, dailyCap) so we never exceed HumanPost's limit."""
    data = mcp_call("list_accounts", {}, token)
    out = {}
    for a in (data.get("data") if isinstance(data, dict) else data) or []:
        out[a["accountId"]] = (a.get("postsToday", 0), a.get("dailyCap", 4))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accounts", default="accounts.json")
    ap.add_argument("--no-git", action="store_true", help="skip git commit/push")
    args = ap.parse_args()

    token = os.environ.get("HUMANPOST_TOKEN", "").strip()
    if not token and not args.dry_run:
        sys.exit("HUMANPOST_TOKEN not set.")

    accounts = json.loads((ROOT / args.accounts).read_text())
    qpath = ROOT / "queue.json"
    queue = json.loads(qpath.read_text())

    caps = {} if (args.dry_run and not token) else caps_by_account(token)

    changed = False
    for h, acct in accounts.items():
        acct_id = acct.get("account_id")
        pend = [c for c in queue["pending"] if c["account"] == h]
        if not pend:
            print("[%s] no pending carousels — run prerender.py to top up." % h)
            continue
        carousel = pend[0]

        posts_today, cap = caps.get(acct_id, (0, acct.get("daily_cap", 4)))
        if posts_today >= cap:
            print("[%s] at daily cap (%d/%d) — skipping today." % (h, posts_today, cap))
            continue

        caption = acct.get("caption", "")
        media_urls = carousel["media_urls"]
        print("[%s] queueing %s (%d slides, caption=%r)"
              % (h, carousel["key"], len(media_urls), caption))
        if args.dry_run:
            for u in media_urls:
                print("    " + u)
            continue

        # Upload each slide in order, then create the carousel.
        upload_ids = []
        for i, url in enumerate(media_urls, 1):
            res = mcp_call("upload_media",
                           {"kind": "image", "sourceUrl": url,
                            "filename": "%s_s%02d.png" % (carousel["key"], i)}, token)
            uid = res.get("uploadId")
            if not uid:
                sys.exit("[%s] upload failed for %s: %r" % (h, url, res))
            upload_ids.append(uid)
        post = mcp_call("create_post",
                        {"media": upload_ids, "accountIds": [acct_id], "caption": caption},
                        token)
        human_id = post.get("postId")
        print("    queued -> HumanPost post %s (status=%s)"
              % (human_id, post.get("status")))

        queue["pending"].remove(carousel)
        carousel["human_post_id"] = human_id
        carousel["posted_at"] = post.get("createdAt")
        queue["posted"].append(carousel)
        changed = True

    if not args.dry_run:
        qpath.write_text(json.dumps(queue, indent=2))

    # Low-queue warning per account.
    from collections import Counter
    per = Counter(c["account"] for c in queue["pending"])
    for h in accounts:
        n = per.get(h, 0)
        if n < LOW_WATERMARK:
            print("::warning:: [%s] only %d pending carousel(s) left — run prerender.py locally." % (h, n))

    if changed and not args.dry_run and not args.no_git:
        run(["git", "config", "user.name", "tweetcards-bot"], check=False)
        run(["git", "config", "user.email", "actions@github.com"], check=False)
        run(["git", "add", "queue.json"])
        run(["git", "commit", "-m", "daily: queue carousels [skip ci]"], check=False)
        run(["git", "pull", "--rebase", "--autostash"], check=False)
        run(["git", "push"], check=False)


if __name__ == "__main__":
    main()
