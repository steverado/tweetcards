#!/usr/bin/env python3
"""
render_cards.py — turn X posts into screenshot-style slides for Instagram carousels.

Reads posts.json (from x_csv_to_posts.py) + accounts.json, filters to the last N
days, ranks by impressions per account, chunks into carousels (2-10 slides, the
range HumanPost's create_post accepts), and screenshots each post at exactly
1080x1350 via headless Chromium.

The output is deliberately NOT a designed card: no rounded corners, shadow, border,
or invented colors. It renders at an iPhone-class viewport (360 CSS px @ 3x) using
only X's own native palette, so structurally it reads as a cropped screenshot.

Writes manifest.json describing each carousel with empty `caption` and `media_urls`
fields to fill in (media_urls is populated by publish_github.py).

Usage:
    python render_cards.py                       # uses posts.json + accounts.json
    python render_cards.py --posts posts.sample.json
    python render_cards.py --days 30 --max-per-carousel 10
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# X's own native palette — no invented colors.
THEMES = {
    "dark": {
        "bg": "#000000", "text": "#e7e9ea", "muted": "#71767b",
        "border": "#2f3336", "link": "#1d9bf0",
    },
    "dim": {
        "bg": "#15202b", "text": "#f7f9f9", "muted": "#8b98a5",
        "border": "#38444d", "link": "#1d9bf0",
    },
    "light": {
        "bg": "#ffffff", "text": "#0f1419", "muted": "#536471",
        "border": "#eff3f4", "link": "#1d9bf0",
    },
}

# Real X action-row icon paths (24x24 viewBox).
ICONS = {
    "reply": "M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z",
    "repost": "M4.75 3.79l4.603 4.3-1.706 1.82L6 8.38v7.37c0 .97.784 1.75 1.75 1.75H13V20H7.75c-2.347 0-4.25-1.9-4.25-4.25V8.38L1.853 9.91.147 8.09l4.603-4.3zm11.5 2.71H11V4h5.25c2.347 0 4.25 1.9 4.25 4.25v7.37l1.647-1.53 1.706 1.82-4.603 4.3-4.603-4.3 1.706-1.82L18 15.62V8.25c0-.97-.784-1.75-1.75-1.75z",
    "like": "M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91z",
    "views": "M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z",
    "share": "M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z",
}

VERIFIED_BADGE = "M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81c-.66-1.31-1.91-2.19-3.34-2.19s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81s-1.26 2.52-.8 3.91c-1.31.67-2.2 1.91-2.2 3.34s.89 2.67 2.2 3.34c-.46 1.39-.21 2.9.8 3.91s2.52 1.26 3.91.81c.67 1.31 1.91 2.19 3.34 2.19s2.68-.88 3.34-2.19c1.39.45 2.9.2 3.91-.81s1.27-2.52.81-3.91c1.31-.67 2.19-1.91 2.19-3.34zm-11.71 4.2L6.8 12.46l1.41-1.42 2.26 2.26 4.8-5.23 1.47 1.36-6.2 6.77z"

CANVAS_W, CANVAS_H = 1080, 1350
CSS_W, CSS_H = 360, 450          # 1080x1350 at deviceScaleFactor 3
SCALE = 3


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def linkify(text):
    """Escape first, THEN linkify. Escaping turns ' into &#x27; so we must not
    run the hashtag/mention regex against escaped entity text (the #x27 bug)."""
    out = []
    for tok in text.split(" "):
        e = esc(tok)
        stripped = tok.strip()
        if stripped.startswith(("#", "@")) and len(stripped) > 1 and stripped[1].isalnum():
            out.append('<span class="lnk">' + e + "</span>")
        elif stripped.startswith(("http://", "https://")):
            out.append('<span class="lnk">' + e + "</span>")
        else:
            out.append(e)
    return " ".join(out).replace("\n", "<br>")


def humanize(n):
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000.0
        return ("%.1f" % v).rstrip("0").rstrip(".") + "K"
    v = n / 1_000_000.0
    return ("%.1f" % v).rstrip("0").rstrip(".") + "M"


def data_uri(path):
    p = ROOT / path
    if not p.exists():
        return None
    ext = p.suffix.lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return "data:image/%s;base64,%s" % (ext, b64)


def avatar_html(acct):
    uri = data_uri(acct.get("avatar", "")) if acct.get("avatar") else None
    if uri:
        return '<div class="avatar"><img src="%s"></div>' % uri
    # Fallback: initials circle so it still renders (swap in the real image later).
    name = acct.get("display_name", "?")
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    return ('<div class="avatar init">%s</div>' % esc(initials))


def icon_svg(name):
    return ('<svg viewBox="0 0 24 24"><path d="%s"></path></svg>' % ICONS[name])


def fmt_meta(created_at, impressions):
    try:
        t = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        time_s = t.strftime("%I:%M %p").lstrip("0")
        date_s = t.strftime("%b ") + str(t.day) + t.strftime(", %Y")
    except Exception:
        time_s, date_s = "", ""
    parts = [p for p in (time_s, date_s) if p]
    left = " · ".join(parts)
    return ('%s · <span class="views">%s</span> Views'
            % (left, humanize(impressions))) if left else \
           ('<span class="views">%s</span> Views' % humanize(impressions))


def build_html(post, acct):
    theme = THEMES[acct["theme"]]
    badge = ""
    if acct.get("verified"):
        badge = ('<svg class="badge" viewBox="0 0 24 24"><path d="%s"></path></svg>'
                 % VERIFIED_BADGE)
    body = linkify(post["text"])
    meta = fmt_meta(post.get("created_at", ""), post.get("impressions", 0))
    actions = ""
    counts = {
        "reply": post.get("replies", 0),
        "repost": post.get("reposts", 0),
        "like": post.get("likes", 0),
        "views": post.get("impressions", 0),
        "share": None,
    }
    for k in ("reply", "repost", "like", "views", "share"):
        c = counts[k]
        label = "" if c is None else '<span>%s</span>' % humanize(c)
        actions += '<div class="act">%s%s</div>' % (icon_svg(k), label)

    tpl = """<!doctype html><html><head><meta charset="utf-8"><style>
:root{--bg:@BG@;--text:@TEXT@;--muted:@MUTED@;--border:@BORDER@;--link:@LINK@;}
*{box-sizing:border-box;-webkit-font-smoothing:antialiased;}
html,body{margin:0;padding:0;}
body{width:@CW@px;height:@CH@px;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  display:flex;align-items:center;justify-content:center;overflow:hidden;}
.tweet{width:100%;padding:12px 16px;}
.head{display:flex;align-items:flex-start;}
.avatar{width:40px;height:40px;border-radius:50%;flex:0 0 40px;overflow:hidden;
  background:#4a4a4a;display:flex;align-items:center;justify-content:center;}
.avatar img{width:100%;height:100%;object-fit:cover;display:block;}
.avatar.init{color:#fff;font-weight:700;font-size:16px;}
.names{margin-left:12px;flex:1 1 auto;min-width:0;line-height:1.25;}
.dname{display:flex;align-items:center;font-weight:800;font-size:15px;color:var(--text);}
.dname .badge{width:16px;height:16px;fill:var(--link);margin-left:2px;flex:0 0 16px;}
.handle{color:var(--muted);font-size:15px;}
.menu{color:var(--muted);flex:0 0 auto;font-weight:700;font-size:18px;line-height:1;
  letter-spacing:1px;margin-left:8px;}
.body{margin-top:12px;font-size:23px;line-height:1.35;color:var(--text);
  white-space:normal;word-wrap:break-word;overflow-wrap:anywhere;}
.lnk{color:var(--link);}
.meta{margin-top:16px;color:var(--muted);font-size:15px;}
.meta .views{color:var(--text);font-weight:700;}
.rule{border-top:1px solid var(--border);margin-top:14px;}
.actions{display:flex;justify-content:space-between;align-items:center;
  margin-top:12px;color:var(--muted);}
.act{display:flex;align-items:center;gap:6px;font-size:13px;}
.act svg{width:18.5px;height:18.5px;fill:currentColor;}
</style></head><body>
<div class="tweet">
  <div class="head">
    @AVATAR@
    <div class="names">
      <div class="dname">@DNAME@@BADGE@</div>
      <div class="handle">@@HANDLE@</div>
    </div>
    <div class="menu">&#8943;</div>
  </div>
  <div class="body" id="body">@BODY@</div>
  <div class="meta">@META@</div>
  <div class="rule"></div>
  <div class="actions">@ACTIONS@</div>
</div>
<script>
// Auto-shrink body the way X does on longer posts: 23 -> 20 -> 17.
(function(){
  var body=document.getElementById('body');
  var tweet=document.querySelector('.tweet');
  var sizes=[23,20,17];
  for(var i=0;i<sizes.length;i++){
    body.style.fontSize=sizes[i]+'px';
    if(tweet.offsetHeight<=@AVAIL@) break;
  }
})();
</script>
</body></html>"""
    repl = {
        "@BG@": theme["bg"], "@TEXT@": theme["text"], "@MUTED@": theme["muted"],
        "@BORDER@": theme["border"], "@LINK@": theme["link"],
        "@CW@": str(CSS_W), "@CH@": str(CSS_H), "@AVAIL@": str(CSS_H - 20),
        "@AVATAR@": avatar_html(acct), "@DNAME@": esc(acct["display_name"]),
        "@BADGE@": badge, "@HANDLE@": esc(acct["handle"]),
        "@BODY@": body, "@META@": meta, "@ACTIONS@": actions,
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def within_days(created_at, days):
    if not days:
        return True
    try:
        t = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    return (now - t).days <= days


def chunk(lst, size):
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", default="posts.json")
    ap.add_argument("--accounts", default="accounts.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--days", type=int, default=30, help="0 = no date filter")
    ap.add_argument("--max-per-carousel", type=int, default=10)
    ap.add_argument("--min-per-carousel", type=int, default=2)
    args = ap.parse_args()

    posts_path = ROOT / args.posts
    if not posts_path.exists():
        sys.exit("No %s — run x_csv_to_posts.py first (or pass --posts posts.sample.json)."
                 % args.posts)

    posts = json.loads(posts_path.read_text())
    accounts = json.loads((ROOT / args.accounts).read_text())

    out_dir = ROOT / args.out
    out_dir.mkdir(exist_ok=True)

    # Group eligible posts per account, ranked by impressions.
    by_acct = {}
    for p in posts:
        h = p.get("account")
        if h not in accounts:
            print("  skip: unknown account %r" % h)
            continue
        if not within_days(p.get("created_at", ""), args.days):
            continue
        if int(p.get("impressions", 0)) < int(accounts[h].get("min_impressions", 0)):
            continue
        by_acct.setdefault(h, []).append(p)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright missing. Run: pip install -r requirements.txt && playwright install chromium")

    manifest = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": CSS_W, "height": CSS_H},
                                device_scale_factor=SCALE)
        for h, plist in by_acct.items():
            acct = accounts[h]
            plist.sort(key=lambda p: int(p.get("impressions", 0)), reverse=True)
            acct_out = out_dir / h
            acct_out.mkdir(parents=True, exist_ok=True)

            for ci, group in enumerate(chunk(plist, args.max_per_carousel), 1):
                if len(group) < args.min_per_carousel:
                    print("  %s carousel %d has %d slide(s) (<%d) — HumanPost needs %d-10; "
                          "will still render, fill or drop before queueing."
                          % (h, ci, len(group), args.min_per_carousel, args.min_per_carousel))
                slides = []
                for si, post in enumerate(group, 1):
                    page.set_content(build_html(post, acct), wait_until="load")
                    fname = "%s_c%d_s%02d.png" % (h, ci, si)
                    fpath = acct_out / fname
                    page.screenshot(path=str(fpath))
                    slides.append({
                        "order": si,
                        "file": str(fpath.relative_to(ROOT)),
                        "post_id": post.get("id"),
                        "impressions": int(post.get("impressions", 0)),
                        "source_url": post.get("url"),
                    })
                    print("  rendered %s (%s impressions)"
                          % (fname, humanize(post.get("impressions", 0))))
                manifest.append({
                    "account": h,
                    "display_name": acct["display_name"],
                    "carousel": ci,
                    "caption": "",          # fill before queueing
                    "media_urls": [],       # populated by publish_github.py
                    "slides": slides,
                })
        browser.close()

    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    total = sum(len(c["slides"]) for c in manifest)
    print("\nDone: %d slide(s) across %d carousel(s) -> %s/"
          % (total, len(manifest), args.out))
    print("Manifest: manifest.json")


if __name__ == "__main__":
    main()
