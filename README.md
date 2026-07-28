# tweetcards

Repurpose top-performing X **meme posts** into Instagram carousels for HumanPost.

Pipeline: **X analytics CSV → normalize → fetch each post's media → screenshot-style
slides (real meme image embedded) → host on GitHub/jsDelivr → queue to HumanPost.**

These accounts post image memes, not text tweets, so each slide is a screenshot of the
actual post — header, the meme image, real timestamp + Views, full action row — built to
look like someone cropped it off their phone (X's native palette, iPhone-class
proportions, no card chrome). The media is pulled by post ID from X's public
syndication endpoint, so no paid API tier is needed.

## Accounts

| handle | display name | theme |
|---|---|---|
| techbromemes | Tech Bro Memes | dark (`#000000`) |
| LiteralMem3s | literal memes | dim (`#15202b`) |
| OldEldersonline | old people online | light (`#ffffff`) |

Edit `accounts.json` to tune display names, themes, `min_impressions` gates, the
`verified` badge, and avatar paths.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage

**1. Normalize each account's X analytics export** (analytics.twitter.com → Export data).
Columns drift year to year, so check the mapping first:

```bash
python x_csv_to_posts.py techbromemes_export.csv --inspect
python x_csv_to_posts.py techbromemes_export.csv --account techbromemes
python x_csv_to_posts.py literalmemes_export.csv  --account LiteralMem3s   --append
python x_csv_to_posts.py oldelders_export.csv      --account OldEldersonline --append
```

Retweets and replies are dropped automatically. Output: `posts.json`.

**2. Render slides** — filters to the last 30 days, ranks by impressions per account,
takes the top `--top` (default 10), fetches each post's meme image, and screenshots
each as a tweet at 1080×1350:

```bash
python render_cards.py                 # top 10 per account, last 30 days
python render_cards.py --top 4         # fewer per account
python render_cards.py --days 0        # no date filter (all-time top)
```

Fetched media is cached in `media/` (gitignored). Output: `out/<handle>/*.png` +
`manifest.json` (slide order, post IDs, impressions, source URLs, plus empty
`caption` / `media_urls` to fill). Posts with no image and no caption are skipped.

**3. Host the images** on GitHub + jsDelivr (needs a GitHub remote on this repo).
URLs are pinned to the commit SHA so they're immutable:

```bash
python publish_github.py --dry-run     # preview CDN URLs
python publish_github.py               # commit, push, write media_urls
```

**4. Queue to HumanPost** — via the MCP connector (see below). `create_post` takes a
carousel of 2–10 images; slide order = post order. Fill each carousel's `caption` in
`manifest.json` first.

## HumanPost MCP

The connector uses a **static Bearer token**, which the claude.ai web "Add custom
connector" flow can't attach (it forces an OAuth registration the server doesn't
support). Add it via the Claude Code CLI instead:

```bash
claude mcp add --transport http humanpost \
  https://us-central1-clickbaitcarousel.cloudfunctions.net/mcp \
  --header "Authorization: Bearer ccb_live_YOUR_KEY"
```

Key: create one at humanpost.co with scopes `accounts:read`, `posts:read`,
`posts:write`, `analytics:read`. Keep it out of git (it lives in the connector
config, not this repo).

## Status / to-do

- [x] Real profile avatars in `avatars/` for all 3 accounts.
- [x] techbromemes + LiteralMem3s CSVs processed; image pipeline verified end to end.
- [ ] Export **OldEldersonline**'s analytics CSV and run step 1 (`--append`).
- [ ] Add a **public** GitHub remote for step 3 (jsDelivr only serves public repos).
- [ ] Add the HumanPost connector and verify `list_accounts`.
- [ ] Fill each carousel's `caption` in `manifest.json` before queueing.

Note: multi-image posts currently use the first image only. Videos use their poster frame.
